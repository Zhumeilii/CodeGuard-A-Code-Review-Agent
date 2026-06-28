"""
PR Reviewer - PR 分析主逻辑

流程：获取 PR diff → 并发审查各文件 → 生成 Summary Comment → 发布到 Provider
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from agents.orchestrator import Orchestrator
from integrations.diff_pipeline import DiffFile, DiffPipelineConfig, DiffPipelineResult, ReviewChunk, build_token_aware_diff
from integrations.git_provider import (
    CommitMeta,
    IncrementalReviewConfig,
    IncrementalReviewInfo,
    InlineSuggestion,
    PRDiff,
    PRInfo,
    PRProvider,
    REVIEW_MARKER_NAME,
    ProviderComment,
    append_review_marker,
    build_review_marker,
)
from integrations.github_pr import GitHubPRProvider
from integrations.line_resolver import resolve_issue_location
from tools.repo_index import ImpactReport, RepoIndex


# severity 图标映射
_SEVERITY_ICON = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
}

# 跳过 unknown 语言的文件
_SKIP_LANGUAGES = {"unknown"}


class PRReviewer:
    """PR 自动化审查"""

    def __init__(
        self,
        github_token: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[PRProvider] = None,
    ):
        if provider is None:
            if not github_token:
                raise ValueError("github_token is required when provider is not supplied")
            provider = GitHubPRProvider(github_token)
        self.provider = provider
        self.model = model
        self.orchestrator = Orchestrator(model=model)

    async def review_pr_by_url(
        self,
        pr_url: str,
        dry_run: bool = False,
        incremental: bool = True,
    ) -> Tuple[str, Optional[str]]:
        """
        通过 PR URL 触发审查

        Args:
            pr_url: GitHub PR URL
            dry_run: True 时只返回 comment 内容，不发布到 GitHub

        Returns:
            (comment_body, comment_url_or_None)
        """
        repo_name, pr_number = self.provider.parse_pr_url(pr_url)
        return await self.review_pr(repo_name, pr_number, dry_run=dry_run, incremental=incremental)

    async def review_pr(
        self,
        repo_name: str,
        pr_number: int,
        dry_run: bool = False,
        incremental: bool = True,
    ) -> Tuple[str, Optional[str]]:
        """
        完整 PR 审查流程

        Returns:
            (comment_body, comment_url_or_None)
        """
        from tracing import get_tracer
        from tracing.exporter import export_trace

        tracer = get_tracer()
        tracer.new_trace()

        async with tracer.async_span("review_pr", {
            "repo": repo_name,
            "pr_number": pr_number,
            "incremental": incremental,
            "dry_run": dry_run,
        }) as root_span:
            result = await self._review_pr_inner(
                repo_name, pr_number, dry_run, incremental, root_span
            )

        # 导出 trace
        trace_file = export_trace(tracer)
        if trace_file:
            print(f"📊 Trace 已保存: {trace_file}")

        return result

    async def _review_pr_inner(
        self,
        repo_name: str,
        pr_number: int,
        dry_run: bool,
        incremental: bool,
        root_span,
    ) -> Tuple[str, Optional[str]]:
        """review_pr 的内部实现（被 tracing span 包裹）"""
        print(f"\n📥 获取 PR #{pr_number} 信息...")
        pr_info = self.provider.get_pr_info(repo_name, pr_number)

        previous_review = self.provider.get_previous_review(repo_name, pr_number, marker=REVIEW_MARKER_NAME)
        diffs, review_mode, previous_head_sha, incremental_info = self._select_review_diffs(
            repo_name,
            pr_number,
            pr_info,
            previous_review,
            incremental=incremental,
        )
        pipeline_result = build_token_aware_diff(diffs, config=DiffPipelineConfig.from_model(self.model))
        reviewable = [
            file for file in pipeline_result.files
            if file.language not in _SKIP_LANGUAGES and file.changed_lines
        ]
        review_chunks = [
            chunk for chunk in pipeline_result.chunks
            if chunk.language not in _SKIP_LANGUAGES
        ]

        root_span.set_attribute("review_mode", review_mode)
        root_span.set_attribute("chunks_count", len(review_chunks))
        root_span.set_attribute("files_count", len(reviewable))

        if not review_chunks:
            if review_mode == "no_new_commits":
                comment = self._format_no_new_changes_comment(pr_info)
            elif review_mode == "threshold_not_met":
                comment = self._format_threshold_not_met_comment(pr_info)
            else:
                comment = self._format_no_reviewable_comment(pr_info)
            comment = self._with_review_marker(
                comment,
                repo_name,
                pr_number,
                pr_info,
                review_mode,
                previous_head_sha,
            )
            if not dry_run:
                url = self.publish_or_update_review_comment(repo_name, pr_number, comment, previous_review)
                return comment, url
            return comment, None

        print(f"📋 PR: {pr_info.title}")
        print(f"👤 作者: {pr_info.author} | {pr_info.base_branch} ← {pr_info.head_branch}") 
        print(
            f"📁 可审查文件: {len(reviewable)}/{len(diffs)} 个 | "
            f"diff chunks: {len(review_chunks)} | tokens≈{pipeline_result.total_tokens} | mode={review_mode}"
        )

        impact_report = self._build_impact_report(repo_name, pr_info, reviewable)

        # 并发审查 token-aware diff chunks
        print(f"\n🔍 并发审查 {len(review_chunks)} 个 diff chunk...")
        tasks = [
            self.orchestrator.review_diff(
                chunk.content,
                context={
                    "file_path": chunk.file_path,
                    "is_pr_diff": True,
                    "diff_chunk_id": chunk.chunk_id,
                    "diff_token_count": chunk.token_count,
                    "source_files": chunk.source_files,
                    "omitted_files": chunk.omitted_files,
                    "repo_context": self._repo_context_for_file(impact_report, chunk.source_files[0]),
                },
            )
            for chunk in review_chunks
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        diff_files_by_path = {file.file_path: file for file in pipeline_result.files}
        for result in results:
            if isinstance(result, dict):
                result["_diff_files"] = diff_files_by_path

        # 生成 Summary Comment
        comment = self._format_pr_comment(
            pr_info,
            review_chunks,
            results,
            impact_report,
            pipeline_result,
            review_mode=review_mode,
            previous_head_sha=previous_head_sha,
            incremental_info=incremental_info,
        )
        comment = self._with_review_marker(
            comment,
            repo_name,
            pr_number,
            pr_info,
            review_mode,
            previous_head_sha,
        )

        if dry_run:
            print("\n[dry-run] 不发布 Comment，仅打印结果")
            inline_issues = self._collect_inline_issues(review_chunks, results)
            if inline_issues:
                print(f"📌 [dry-run] 检测到 {len(inline_issues)} 条可 inline 的 Critical/High issue")
            return comment, None

        print(f"\n💬 发布 Review Comment...")
        url = self.publish_or_update_review_comment(repo_name, pr_number, comment, previous_review)
        print(f"✅ Comment 已发布: {url}")

        # 发布 inline comments（仅 critical/high 且有行号的 issue）
        inline_issues = self._collect_inline_issues(review_chunks, results)
        if inline_issues:
            print(f"📌 发布 {len(inline_issues)} 条 inline comment...")
            published = self._publish_inline_comments(repo_name, pr_number, inline_issues)
            print(f"✅ Inline comments 已发布: {published}/{len(inline_issues)}")

        return comment, url

    def _select_review_diffs(
        self,
        repo_name: str,
        pr_number: int,
        pr_info: PRInfo,
        previous_review: Optional[ProviderComment],
        incremental: bool,
    ) -> Tuple[List[PRDiff], str, Optional[str], Optional[IncrementalReviewInfo]]:
        previous_head_sha = previous_review.reviewed_head_sha if previous_review else None
        current_head_sha = pr_info.head_sha

        if incremental and previous_head_sha and current_head_sha and previous_head_sha != current_head_sha:
            config = IncrementalReviewConfig()

            # 获取 commit 范围用于阈值检查和摘要
            try:
                all_commits = self.provider.get_commit_range(
                    repo_name, previous_head_sha, current_head_sha, skip_merge_commits=False
                )
            except Exception:
                all_commits = []

            non_merge = [c for c in all_commits if not c.is_merge]
            merge_count = len(all_commits) - len(non_merge)

            # 阈值检查：新 commit 数不足则跳过
            if config.minimal_commits > 0 and len(non_merge) < config.minimal_commits:
                print(
                    f"ℹ️ 增量审查阈值未达到（commits={len(non_merge)}, "
                    f"需要>={config.minimal_commits}），跳过"
                )
                return [], "threshold_not_met", previous_head_sha, None

            try:
                print(f"🔁 增量 Review: {previous_head_sha[:8]}...{current_head_sha[:8]}")
                diffs = self.provider.get_incremental_diff_files(
                    repo_name,
                    pr_number,
                    previous_head_sha,
                    current_head_sha,
                )
                filtered_commits = non_merge if config.skip_merge_commits else all_commits
                info = IncrementalReviewInfo(
                    last_reviewed_sha=previous_head_sha,
                    current_head_sha=current_head_sha,
                    commits_range=filtered_commits,
                    first_new_commit_sha=filtered_commits[0].sha if filtered_commits else "",
                    skipped_merge_commits=merge_count if config.skip_merge_commits else 0,
                )
                return diffs, "incremental", previous_head_sha, info
            except Exception as e:
                print(f"⚠️ 增量 diff 获取失败，回退完整 Review: {e}")

        if incremental and previous_head_sha == current_head_sha and current_head_sha:
            print("ℹ️ head SHA 与上次 Review 一致，没有新的 commit 需要审查")
            return [], "no_new_commits", previous_head_sha, None

        return pr_info.diffs or self.provider.get_diff_files(repo_name, pr_number), "full", previous_head_sha, None

    def publish_or_update_review_comment(
        self,
        repo_name: str,
        pr_number: int,
        body: str,
        previous_review: Optional[ProviderComment] = None,
    ) -> str:
        previous_review = previous_review or self.provider.get_previous_review(
            repo_name,
            pr_number,
            marker=REVIEW_MARKER_NAME,
        )
        if previous_review:
            return self.provider.update_comment(repo_name, previous_review.id, body)
        return self.provider.publish_comment(repo_name, pr_number, body)

    def _with_review_marker(
        self,
        body: str,
        repo_name: str,
        pr_number: int,
        pr_info: PRInfo,
        review_mode: str,
        previous_head_sha: Optional[str],
    ) -> str:
        marker = build_review_marker(
            repo_name=repo_name,
            pr_number=pr_number,
            head_sha=pr_info.head_sha,
            mode=review_mode,
            base_sha=previous_head_sha,
        )
        return append_review_marker(body, marker)

    def _build_impact_report(
        self,
        repo_name: str,
        pr_info: PRInfo,
        diffs: List[DiffFile],
    ) -> Optional[ImpactReport]:
        """Build repo-level impact analysis for PR head revision."""
        try:
            print("\n🧭 构建 repo-level impact analysis...")
            sources = self.provider.get_repo_source_snapshot(
                pr_info.head_repo_full_name or repo_name,
                self._head_ref_for_snapshot(pr_info),
            )
            index = RepoIndex.build(sources)
            changed_files = [(diff.file_path, diff.language, diff.patch) for diff in diffs]
            report = index.analyze_impact(changed_files)
            print(
                "✅ Impact analysis 完成: "
                f"{report.indexed_files} files, {report.indexed_symbols} symbols, risk={report.risk_level}"
            )
            return report
        except Exception as e:
            print(f"⚠️ Impact analysis 失败，继续常规审查: {e}")
            return None

    def _head_ref_for_snapshot(self, pr_info: PRInfo) -> str:
        """Return the best ref for fetching PR head source snapshot."""
        return pr_info.head_sha or pr_info.head_branch

    def _repo_context_for_file(self, impact_report: Optional[ImpactReport], file_path: str) -> str:
        if not impact_report:
            return ""
        relevant = [item for item in impact_report.items if item.file_path == file_path]
        if not relevant:
            return impact_report.to_context(max_symbols_per_file=4)
        return ImpactReport(
            items=relevant,
            parser=impact_report.parser,
            indexed_files=impact_report.indexed_files,
            indexed_symbols=impact_report.indexed_symbols,
        ).to_context(max_symbols_per_file=8)

    # ── Comment 格式化 ────────────────────────────────────

    def _format_pr_comment(
        self,
        pr_info: PRInfo,
        diffs: List[ReviewChunk],
        results: List[Any],
        impact_report: Optional[ImpactReport] = None,
        pipeline_result: Optional[DiffPipelineResult] = None,
        review_mode: str = "full",
        previous_head_sha: Optional[str] = None,
        incremental_info: Optional[IncrementalReviewInfo] = None,
    ) -> str:
        """生成 Markdown 格式的 PR Summary Comment"""
        sections = []

        # 头部
        sections.append(self._format_header(pr_info, len(diffs), review_mode=review_mode))

        # 增量审查时插入 commit 摘要
        if review_mode == "incremental" and incremental_info:
            commit_summary = self._format_commit_summary(incremental_info)
            if commit_summary:
                sections.append(commit_summary)

        # 汇总表格
        table, file_issues = self._build_summary_table(diffs, results)
        sections.append(table)

        if impact_report:
            sections.append(impact_report.to_comment_markdown())
        if pipeline_result:
            sections.append(self._format_diff_pipeline_summary(pipeline_result, review_mode, previous_head_sha))

        # 按 severity 分组输出问题
        critical_high = self._collect_issues(file_issues, ("critical", "high"))
        medium = self._collect_issues(file_issues, ("medium",))
        low = self._collect_issues(file_issues, ("low",))

        if critical_high:
            sections.append(self._format_issue_section("🔴 Critical / High Issues", critical_high))
        if medium:
            sections.append(self._format_issue_section("🟡 Warnings", medium))
        if low:
            sections.append(self._format_issue_section("💡 Suggestions", low, collapsible=True))

        # 无问题时的正面反馈
        if not critical_high and not medium and not low:
            sections.append("\n### ✅ LGTM!\n\n未发现明显问题，代码质量良好。")

        # 页脚
        sections.append(self._format_footer())

        return "\n\n".join(sections)

    def _format_header(self, pr_info: PRInfo, file_count: int, review_mode: str = "full") -> str:
        if review_mode == "incremental":
            title = "## 🔁 AI Incremental Code Review"
            mode_badge = "增量审查"
        else:
            title = "## 🤖 AI Code Review"
            mode_badge = "完整审查"
        return (
            f"{title}\n\n"
            f"> **PR #{pr_info.number}**: {pr_info.title}  \n"
            f"> **作者**: @{pr_info.author} · "
            f"`{pr_info.base_branch}` ← `{pr_info.head_branch}`  \n"
            f"> **审查模式**: {mode_badge} · **分析文件**: {file_count} 个\n\n"
            f"---"
        )

    def _build_summary_table(
        self, diffs: List[ReviewChunk], results: List[Any]
    ) -> Tuple[str, List[Dict]]:
        """
        构建汇总表格，同时收集每个文件的问题列表

        Returns:
            (table_markdown, file_issues_list)
            file_issues_list: [{"file": str, "issues": [{"severity", "source", "line", "message", "fix_hint"}]}]
        """
        rows = []
        file_issues = []

        for diff, result in zip(diffs, results):
            fname = f"`{diff.file_path}`"

            if isinstance(result, Exception):
                rows.append(f"| {fname} | ❌ 分析失败 | — | — | — |")
                file_issues.append({"file": diff.file_path, "issues": []})
                continue
            diff_file = self._diff_file_for_chunk(diff, result)

            # 提取各维度分数和问题（兼容旧 5-agent 结果）
            correctness_issues = self._correctness_issues(result)
            security_issues = self._security_issues(result)
            maintainability = result.get("maintainability", {})
            maintainability_score = maintainability.get("score", result.get("quality", {}).get("score", "—"))
            maintainability_issues = self._maintainability_issues(result)

            def fmt_cell(items, key="severity"):
                if not items:
                    return "✅"
                highs = [i for i in items if i.get(key) in ("critical", "high")]
                return f"⚠️ {len(items)}" + (f" ({len(highs)} high)" if highs else "")

            rows.append(
                f"| {fname} | {fmt_cell(correctness_issues)} | "
                f"{fmt_cell(security_issues)} | {maintainability_score} | "
                f"{fmt_cell(maintainability_issues)} |"
            )

            # 收集所有问题
            issues = []
            for item in correctness_issues:
                issues.append(resolve_issue_location({
                    "severity": item.get("severity", "medium"),
                    "source": "correctness",
                    "line": item.get("line"),
                    "existing_code": item.get("existing_code") or item.get("evidence", ""),
                    "message": item.get("message", ""),
                    "fix_hint": item.get("recommendation") or item.get("fix", ""),
                }, diff_file))
            for item in security_issues:
                issues.append(resolve_issue_location({
                    "severity": item.get("severity", "high"),
                    "source": "security",
                    "line": item.get("line"),
                    "existing_code": item.get("existing_code") or item.get("evidence", ""),
                    "message": item.get("message", ""),
                    "fix_hint": item.get("recommendation") or item.get("remediation", ""),
                }, diff_file))
            for item in maintainability_issues:
                issues.append(resolve_issue_location({
                    "severity": item.get("severity", "low"),
                    "source": "maintainability",
                    "line": item.get("line"),
                    "existing_code": item.get("existing_code", ""),
                    "message": item.get("message", ""),
                    "fix_hint": item.get("recommendation") or item.get("suggestion", ""),
                }, diff_file))

            file_issues.append({"file": diff.file_path, "issues": issues})

        header = "### 📊 Summary\n\n| 文件 | 正确性 | 安全 | 维护性分 | 维护性问题 |\n|------|--------|------|----------|------------|"
        table = header + "\n" + "\n".join(rows)
        return table, file_issues

    def _correctness_issues(self, result: Dict[str, Any]) -> List[Dict]:
        issues = list(result.get("correctness", {}).get("findings", []))
        issues.extend(result.get("correctness", {}).get("issues", []))
        issues.extend(result.get("bug", {}).get("bugs", []))
        for item in result.get("perf", {}).get("issues", []):
            normalized = dict(item)
            normalized.setdefault("recommendation", item.get("optimization", ""))
            issues.append(normalized)
        return issues

    def _security_issues(self, result: Dict[str, Any]) -> List[Dict]:
        issues = list(result.get("security", {}).get("findings", []))
        for item in result.get("security", {}).get("vulnerabilities", []):
            normalized = dict(item)
            normalized.setdefault("recommendation", item.get("remediation", ""))
            issues.append(normalized)
        return issues

    def _maintainability_issues(self, result: Dict[str, Any]) -> List[Dict]:
        issues = list(result.get("maintainability", {}).get("findings", []))
        issues.extend(result.get("maintainability", {}).get("issues", []))
        issues.extend(result.get("quality", {}).get("issues", []))
        for item in result.get("practice", {}).get("recommendations", []):
            normalized = dict(item)
            normalized["severity"] = item.get("severity", item.get("priority", "low"))
            normalized.setdefault("recommendation", item.get("best_practice", ""))
            issues.append(normalized)
        return issues

    def _diff_file_for_chunk(self, chunk: ReviewChunk, result: Any) -> Optional[DiffFile]:
        """Find parsed diff metadata attached to this chunk's review result."""
        if not isinstance(result, dict):
            return None
        diff_files = result.get("_diff_files") or {}
        file_path = chunk.source_files[0] if chunk.source_files else chunk.file_path.split("#")[0]
        return diff_files.get(file_path)

    def _format_diff_pipeline_summary(
        self,
        pipeline_result: DiffPipelineResult,
        review_mode: str,
        previous_head_sha: Optional[str],
    ) -> str:
        lines = [
            "### Diff Processing",
            "",
            f"- Review mode: {review_mode}",
            f"- Review chunks: {len(pipeline_result.chunks)}",
            f"- Estimated input tokens: {pipeline_result.total_tokens}",
            f"- Skipped files: {len(pipeline_result.skipped_files)}",
            f"- Omitted files: {len(pipeline_result.omitted_files)}",
        ]
        if previous_head_sha and review_mode == "incremental":
            lines.append(f"- Previous reviewed head: `{previous_head_sha[:12]}`")
        if pipeline_result.skipped_files:
            skipped = ", ".join(
                f"`{file.file_path}` ({file.skipped_reason})"
                for file in pipeline_result.skipped_files[:8]
            )
            lines.append(f"- Skipped detail: {skipped}")
        if pipeline_result.omitted_files:
            omitted = ", ".join(f"`{path}`" for path in pipeline_result.omitted_files[:8])
            lines.append(f"- Omitted detail: {omitted}")
        return "\n".join(lines)

    def _collect_issues(
        self, file_issues: List[Dict], severities: Tuple[str, ...]
    ) -> List[Dict]:
        """收集指定 severity 的问题，按文件分组"""
        result = []
        for fi in file_issues:
            matched = [i for i in fi["issues"] if i["severity"] in severities]
            if matched:
                result.append({"file": fi["file"], "issues": matched})
        return result

    def _format_issue_section(
        self, title: str, file_issues: List[Dict], collapsible: bool = False
    ) -> str:
        lines = [f"### {title}\n"]
        for fi in file_issues:
            lines.append(f"**`{fi['file']}`**")
            for issue in fi["issues"]:
                icon = _SEVERITY_ICON.get(issue["severity"], "•")
                line_ref = f"Line {issue['line']}: " if issue.get("line") else ""
                source = f"[{issue['source']}] "
                hint = f" — _{issue['fix_hint']}_" if issue.get("fix_hint") else ""
                lines.append(f"- {icon} {source}{line_ref}{issue['message']}{hint}")
            lines.append("")

        content = "\n".join(lines)
        if collapsible:
            return f"<details>\n<summary>{title}</summary>\n\n{content}\n</details>"
        return content

    def _format_no_reviewable_comment(self, pr_info: PRInfo) -> str:
        return (
            f"## 🤖 AI Code Review\n\n"
            f"> PR #{pr_info.number}: {pr_info.title}\n\n"
            f"ℹ️ 此 PR 中没有可审查的代码文件（可能只包含配置文件、lock 文件或删除操作）。\n\n"
            f"{self._format_footer()}"
        )

    def _format_no_new_changes_comment(self, pr_info: PRInfo) -> str:
        return (
            f"## 🤖 AI Code Review\n\n"
            f"> PR #{pr_info.number}: {pr_info.title}\n\n"
            f"ℹ️ 当前 head commit 已经审查过，没有新的 commit 需要增量审查。\n\n"
            f"{self._format_footer()}"
        )

    def _format_threshold_not_met_comment(self, pr_info: PRInfo) -> str:
        return (
            f"## 🤖 AI Code Review\n\n"
            f"> PR #{pr_info.number}: {pr_info.title}\n\n"
            f"ℹ️ 增量审查阈值未达到，暂不触发审查。新 commit 积累到配置阈值后将自动触发。\n\n"
            f"{self._format_footer()}"
        )

    def _format_commit_summary(self, incremental_info: IncrementalReviewInfo) -> str:
        """生成增量审查的 commit 范围摘要"""
        if not incremental_info.commits_range:
            return ""
        lines = [
            "### 📝 本次审查的 Commit",
            "",
            (
                f"审查范围: `{incremental_info.last_reviewed_sha[:8]}` → "
                f"`{incremental_info.current_head_sha[:8]}`"
            ),
            "",
            "| SHA | 作者 | 提交信息 |",
            "|-----|------|----------|",
        ]
        for commit in incremental_info.commits_range[:20]:
            msg = commit.message[:60] + ("..." if len(commit.message) > 60 else "")
            lines.append(f"| `{commit.short_sha}` | @{commit.author} | {msg} |")
        if len(incremental_info.commits_range) > 20:
            lines.append(f"| ... | | _还有 {len(incremental_info.commits_range) - 20} 个 commit_ |")
        if incremental_info.skipped_merge_commits > 0:
            lines.append(f"\n_已跳过 {incremental_info.skipped_merge_commits} 个 merge commit_")
        return "\n".join(lines)

    # ── Inline Comment 逻辑 ────────────────────────────────────

    _INLINE_SEVERITY = {"critical", "high"}
    _SOURCE_LABEL = {
        "correctness": ("🧭", "Correctness"),
        "bug": ("🐛", "Bug"),
        "security": ("🔒", "Security"),
        "perf": ("⚡", "Performance"),
        "maintainability": ("🛠", "Maintainability"),
        "quality": ("📝", "Quality"),
        "practice": ("💡", "Practice"),
    }

    def _collect_inline_issues(
        self, chunks: List[ReviewChunk], results: List[Any]
    ) -> List[Dict]:
        """收集适合发布 inline comment 的 issue（critical/high + 可定位行号）"""
        inline_issues: List[Dict] = []

        for chunk, result in zip(chunks, results):
            if isinstance(result, Exception):
                continue
            file_path = chunk.source_files[0] if chunk.source_files else chunk.file_path.split("#")[0]
            diff_file = self._diff_file_for_chunk(chunk, result)

            specs = [
                ("correctness", self._correctness_issues(result), "recommendation"),
                ("security", self._security_issues(result), "recommendation"),
            ]
            for source, items, suggestion_key in specs:
                for item in items:
                    issue = resolve_issue_location({
                        "file_path": file_path,
                        "line": item.get("line"),
                        "source": source,
                        "type": item.get("type", ""),
                        "severity": item.get("severity", ""),
                        "message": item.get("message", ""),
                        "evidence": item.get("evidence", ""),
                        "existing_code": item.get("existing_code") or item.get("evidence", ""),
                        "suggestion": item.get(suggestion_key, ""),
                    }, diff_file)
                    if issue.get("severity") in self._INLINE_SEVERITY and issue.get("line"):
                        inline_issues.append(issue)

        return inline_issues

    def _format_inline_body(self, issue: Dict) -> str:
        """格式化单条 inline comment 内容"""
        icon, label = self._SOURCE_LABEL.get(issue["source"], ("•", issue["source"]))
        severity_icon = _SEVERITY_ICON.get(issue["severity"], "🔴")
        issue_type = issue.get("type", "")
        type_display = f" — {issue_type}" if issue_type else ""

        lines = [
            f"{severity_icon} **[{label}]**{type_display}",
            "",
            f"**Problem:** {issue['message']}",
        ]

        if issue.get("evidence"):
            lines.extend([
                "",
                "**Evidence:**",
                f"> {issue['evidence']}",
            ])

        if issue.get("suggestion"):
            lines.extend([
                "",
                f"**Suggestion:** {issue['suggestion']}",
            ])

        return "\n".join(lines)

    def _publish_inline_comments(
        self, repo_name: str, pr_number: int, inline_issues: List[Dict]
    ) -> int:
        """发布 inline comments，返回成功发布数"""
        published = 0
        for issue in inline_issues:
            try:
                suggestion = InlineSuggestion(
                    file_path=issue["file_path"],
                    line=issue["line"],
                    body=self._format_inline_body(issue),
                )
                self.provider.publish_inline_suggestion(repo_name, pr_number, suggestion)
                published += 1
            except Exception as e:
                print(f"  ⚠️ Inline comment 发布失败 ({issue['file_path']}:{issue['line']}): {e}")
        return published

    def _format_footer(self) -> str:
        return (
            "\n---\n"
            "<sub>🤖 由 [code-review-agent](https://github.com) 自动生成 · "
            "如有误报请忽略</sub>"
        )
