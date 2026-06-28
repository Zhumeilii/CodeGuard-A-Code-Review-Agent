#!/usr/bin/env python3
"""PR provider abstraction tests."""
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from integrations.git_provider import (
    CommitMeta,
    IncrementalReviewConfig,
    IncrementalReviewInfo,
    InlineSuggestion,
    PRDiff,
    PRInfo,
    PRProvider,
    ProviderComment,
    build_review_marker,
    parse_review_marker,
)
from integrations import pr_reviewer


class FakeProvider(PRProvider):
    def __init__(self, diffs=None, previous_review=None, head_sha="head-new", incremental_diffs=None, commit_range=None):
        self.diffs = diffs if diffs is not None else [
            PRDiff(
                file_path="src/app.py",
                language="python",
                patch="@@ -1 +1 @@\n+print('hello')",
                added_lines="print('hello')",
                full_content="print('hello')",
                status="modified",
            )
        ]
        self.incremental_diffs = incremental_diffs if incremental_diffs is not None else self.diffs
        self.commit_range_data = commit_range if commit_range is not None else []
        self.previous_review = previous_review
        self.head_sha = head_sha
        self.published_comments = []
        self.updated_comments = []
        self.published_labels = []
        self.inline_suggestions = []
        self.diff_files_requested = False
        self.incremental_diff_requested = False
        self.commit_range_requested = False

    def parse_pr_url(self, url):
        return "owner/repo", 7

    def get_pr_info(self, repo_name, pr_number):
        return PRInfo(
            number=pr_number,
            title="Provider abstraction",
            body="",
            author="alice",
            base_branch="main",
            head_branch="feature",
            repo_full_name=repo_name,
            html_url=f"https://example.test/{repo_name}/pull/{pr_number}",
            head_sha=self.head_sha,
            diffs=[],
        )

    def get_diff_files(self, repo_name, pr_number):
        self.diff_files_requested = True
        return self.diffs

    def publish_comment(self, repo_name, pr_number, body):
        self.published_comments.append((repo_name, pr_number, body))
        return "https://example.test/comment/1"

    def update_comment(self, repo_name, comment_id, body):
        self.updated_comments.append((repo_name, comment_id, body))
        return f"https://example.test/comment/{comment_id}"

    def publish_inline_suggestion(self, repo_name, pr_number, suggestion):
        self.inline_suggestions.append((repo_name, pr_number, suggestion))
        return "https://example.test/comment/inline"

    def publish_labels(self, repo_name, pr_number, labels):
        self.published_labels = list(labels)
        return self.published_labels

    def get_previous_review(self, repo_name, pr_number, marker="code-review-agent"):
        return self.previous_review

    def get_incremental_diff_files(self, repo_name, pr_number, base_sha, head_sha):
        self.incremental_diff_requested = True
        return self.incremental_diffs

    def get_commit_range(self, repo_name, base_sha, head_sha, skip_merge_commits=True):
        self.commit_range_requested = True
        if skip_merge_commits:
            return [c for c in self.commit_range_data if not c.is_merge]
        return self.commit_range_data


class FakeOrchestrator:
    calls = []

    def __init__(self):
        self.calls = FakeOrchestrator.calls

    async def review_code(self, code, language, context=None):
        self.calls.append(("code", code, language, context or {}))
        return {
            "quality": {"score": 100, "issues": []},
            "bug": {"bugs": []},
            "security": {"vulnerabilities": []},
            "perf": {"issues": []},
            "practice": {"recommendations": []},
        }

    async def review_diff(self, diff, context=None, enabled_agents=None):
        self.calls.append(("diff", diff, "diff", context or {}))
        return {
            "quality": {"score": 100, "issues": []},
            "bug": {"bugs": []},
            "security": {"vulnerabilities": []},
            "perf": {"issues": []},
            "practice": {"recommendations": []},
        }


def test_pr_reviewer_uses_provider_interface(monkeypatch):
    FakeOrchestrator.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestrator())
    provider = FakeProvider()
    reviewer = pr_reviewer.PRReviewer(provider=provider)
    monkeypatch.setattr(reviewer, "_build_impact_report", lambda *args, **kwargs: None)

    comment, url = asyncio.run(reviewer.review_pr_by_url("https://example.test/owner/repo/pull/7", dry_run=True))

    assert url is None
    assert provider.diff_files_requested
    assert "AI Code Review" in comment
    assert "src/app.py" in comment
    assert not provider.published_comments
    assert FakeOrchestrator.calls
    call_type, diff_payload, language, context = FakeOrchestrator.calls[0]
    assert call_type == "diff"
    assert language == "diff"
    assert "Annotated unified diff" in diff_payload
    assert "diff_chunk_id" in context


def test_pr_reviewer_publishes_comment_through_provider(monkeypatch):
    FakeOrchestrator.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestrator())
    provider = FakeProvider(diffs=[])
    reviewer = pr_reviewer.PRReviewer(provider=provider)

    comment, url = asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=False))

    assert url == "https://example.test/comment/1"
    assert len(provider.published_comments) == 1
    assert provider.published_comments[0][0] == "owner/repo"
    assert provider.published_comments[0][1] == 7
    assert comment == provider.published_comments[0][2]
    assert "code-review-agent:review" in comment
    assert parse_review_marker(comment)["head"] == "head-new"


def test_provider_contract_supports_inline_suggestions_and_labels():
    provider = FakeProvider()
    suggestion = InlineSuggestion(
        file_path="src/app.py",
        line=3,
        body="Consider extracting this branch into a helper.",
    )

    inline_url = provider.publish_inline_suggestion("owner/repo", 7, suggestion)
    labels = provider.publish_labels("owner/repo", 7, ["reviewed", "security"])

    assert inline_url.endswith("/inline")
    assert provider.inline_suggestions[0][2] == suggestion
    assert labels == ["reviewed", "security"]


def test_pr_reviewer_updates_previous_review_comment(monkeypatch):
    FakeOrchestrator.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestrator())
    marker = build_review_marker(
        repo_name="owner/repo",
        pr_number=7,
        head_sha="head-old",
        mode="full",
    )
    previous = ProviderComment(id=42, body=f"old body\n\n{marker}", html_url="https://example.test/comment/42")
    provider = FakeProvider(previous_review=previous, head_sha="head-new")
    reviewer = pr_reviewer.PRReviewer(provider=provider)
    monkeypatch.setattr(reviewer, "_build_impact_report", lambda *args, **kwargs: None)

    comment, url = asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=False, incremental=True))

    assert url == "https://example.test/comment/42"
    assert not provider.published_comments
    assert len(provider.updated_comments) == 1
    assert provider.incremental_diff_requested
    marker_data = parse_review_marker(comment)
    assert marker_data["head"] == "head-new"
    assert marker_data["base"] == "head-old"
    assert marker_data["mode"] == "incremental"


def test_pr_reviewer_skips_when_head_already_reviewed(monkeypatch):
    FakeOrchestrator.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestrator())
    marker = build_review_marker(
        repo_name="owner/repo",
        pr_number=7,
        head_sha="same-head",
        mode="incremental",
    )
    previous = ProviderComment(id=42, body=f"old body\n\n{marker}", html_url="https://example.test/comment/42")
    provider = FakeProvider(previous_review=previous, head_sha="same-head")
    reviewer = pr_reviewer.PRReviewer(provider=provider)

    comment, url = asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=False, incremental=True))

    assert url == "https://example.test/comment/42"
    assert provider.updated_comments
    assert not provider.incremental_diff_requested
    assert not FakeOrchestrator.calls
    assert "没有新的 commit" in comment
    assert parse_review_marker(comment)["mode"] == "no_new_commits"


def test_incremental_review_includes_commit_summary(monkeypatch):
    """增量审查 comment 中包含 commit 摘要表格"""
    FakeOrchestrator.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestrator())
    marker = build_review_marker(
        repo_name="owner/repo", pr_number=7, head_sha="head-old", mode="full"
    )
    previous = ProviderComment(id=42, body=f"old body\n\n{marker}", html_url="https://example.test/comment/42")
    commits = [
        CommitMeta(sha="aaa11111", short_sha="aaa11111", message="fix: resolve bug", author="bob", is_merge=False),
        CommitMeta(sha="bbb22222", short_sha="bbb22222", message="feat: add feature", author="alice", is_merge=False),
    ]
    provider = FakeProvider(previous_review=previous, head_sha="head-new", commit_range=commits)
    reviewer = pr_reviewer.PRReviewer(provider=provider)
    monkeypatch.setattr(reviewer, "_build_impact_report", lambda *args, **kwargs: None)

    comment, url = asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=True, incremental=True))

    assert "本次审查的 Commit" in comment
    assert "aaa11111" in comment
    assert "fix: resolve bug" in comment
    assert "@bob" in comment
    assert "Incremental Code Review" in comment
    assert provider.commit_range_requested


def test_threshold_not_met_skips_review(monkeypatch):
    """commit 阈值未达到时跳过增量审查"""
    FakeOrchestrator.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestrator())
    # 设置阈值为 5 个 commit
    monkeypatch.setenv("INCREMENTAL_MIN_COMMITS", "5")
    marker = build_review_marker(
        repo_name="owner/repo", pr_number=7, head_sha="head-old", mode="full"
    )
    previous = ProviderComment(id=42, body=f"old\n\n{marker}", html_url="https://example.test/comment/42")
    commits = [
        CommitMeta(sha="aaa11111", short_sha="aaa11111", message="small fix", author="bob", is_merge=False),
    ]
    provider = FakeProvider(previous_review=previous, head_sha="head-new", commit_range=commits)
    reviewer = pr_reviewer.PRReviewer(provider=provider)

    comment, url = asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=True, incremental=True))

    assert "阈值未达到" in comment
    assert parse_review_marker(comment)["mode"] == "threshold_not_met"
    assert not provider.incremental_diff_requested
    assert not FakeOrchestrator.calls


def test_force_full_review_bypasses_incremental(monkeypatch):
    """incremental=False 时即使有 previous review 也执行全量审查"""
    FakeOrchestrator.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestrator())
    marker = build_review_marker(
        repo_name="owner/repo", pr_number=7, head_sha="head-old", mode="incremental"
    )
    previous = ProviderComment(id=42, body=f"old\n\n{marker}", html_url="https://example.test/comment/42")
    provider = FakeProvider(previous_review=previous, head_sha="head-new")
    reviewer = pr_reviewer.PRReviewer(provider=provider)
    monkeypatch.setattr(reviewer, "_build_impact_report", lambda *args, **kwargs: None)

    comment, url = asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=True, incremental=False))

    assert not provider.incremental_diff_requested
    assert provider.diff_files_requested
    assert parse_review_marker(comment)["mode"] == "full"
    assert "AI Code Review" in comment


# ── Inline Comment 测试 ────────────────────────────────────


class FakeOrchestratorWithBugs:
    """返回包含 critical bug 的 mock orchestrator"""
    calls = []

    def __init__(self):
        self.calls = FakeOrchestratorWithBugs.calls

    async def review_code(self, code, language, context=None):
        return {}

    async def review_diff(self, diff, context=None, enabled_agents=None):
        self.calls.append(("diff", diff, "diff", context or {}))
        return {
            "quality": {"score": 80, "issues": []},
            "bug": {"bugs": [
                {
                    "type": "boundary",
                    "severity": "critical",
                    "line": 5,
                    "message": "数组越界",
                    "evidence": "data[index] 未做边界检查",
                    "impact": "crash",
                    "fix": "添加 index 范围校验",
                },
                {
                    "type": "logic",
                    "severity": "low",
                    "line": 10,
                    "message": "冗余变量",
                    "evidence": "",
                    "impact": "无",
                    "fix": "删除未使用变量",
                },
            ]},
            "security": {"vulnerabilities": [
                {
                    "type": "injection",
                    "severity": "high",
                    "line": 8,
                    "message": "SQL 注入风险",
                    "evidence": "f\"SELECT * FROM users WHERE id={user_input}\"",
                    "remediation": "使用参数化查询",
                },
            ]},
            "perf": {"issues": []},
            "practice": {"recommendations": []},
        }


def test_inline_comments_published_for_critical_issues(monkeypatch):
    """critical/high issue 被正确发布为 inline comment"""
    FakeOrchestratorWithBugs.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestratorWithBugs())
    provider = FakeProvider()
    reviewer = pr_reviewer.PRReviewer(provider=provider)
    monkeypatch.setattr(reviewer, "_build_impact_report", lambda *args, **kwargs: None)

    comment, url = asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=False))

    # 应发布 2 条 inline：1 critical correctness issue + 1 high security
    assert len(provider.inline_suggestions) == 2
    # 第一条 inline 是 critical correctness issue
    _, _, suggestion1 = provider.inline_suggestions[0]
    assert suggestion1.line == 5
    assert suggestion1.file_path == "src/app.py"
    assert "Correctness" in suggestion1.body
    assert "数组越界" in suggestion1.body
    assert "Evidence" in suggestion1.body
    # 第二条是 security
    _, _, suggestion2 = provider.inline_suggestions[1]
    assert suggestion2.line == 8
    assert "Security" in suggestion2.body
    assert "SQL 注入" in suggestion2.body


def test_inline_skipped_for_low_severity(monkeypatch):
    """low/medium 级别的 issue 不发 inline comment"""
    FakeOrchestratorWithBugs.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestratorWithBugs())
    provider = FakeProvider()
    reviewer = pr_reviewer.PRReviewer(provider=provider)
    monkeypatch.setattr(reviewer, "_build_impact_report", lambda *args, **kwargs: None)

    asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=False))

    # 确认 low severity 的 bug (line 10) 没有被发 inline
    inline_lines = [s[2].line for s in provider.inline_suggestions]
    assert 10 not in inline_lines


def test_inline_skipped_without_line_number(monkeypatch):
    """没有行号的 issue 不发 inline comment"""

    class NoLineOrchestrator:
        async def review_diff(self, diff, context=None, enabled_agents=None):
            return {
                "quality": {"score": 80, "issues": []},
                "bug": {"bugs": [{
                    "type": "logic",
                    "severity": "critical",
                    "line": None,  # 无行号
                    "message": "潜在问题",
                    "evidence": "",
                    "impact": "",
                    "fix": "",
                }]},
                "security": {"vulnerabilities": []},
                "perf": {"issues": []},
                "practice": {"recommendations": []},
            }

    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: NoLineOrchestrator())
    provider = FakeProvider()
    reviewer = pr_reviewer.PRReviewer(provider=provider)
    monkeypatch.setattr(reviewer, "_build_impact_report", lambda *args, **kwargs: None)

    asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=False))

    assert len(provider.inline_suggestions) == 0


def test_inline_failure_does_not_block_summary(monkeypatch):
    """inline 发布失败不影响 summary comment"""

    class FailingInlineProvider(FakeProvider):
        def publish_inline_suggestion(self, repo_name, pr_number, suggestion):
            raise RuntimeError("GitHub API 500")

    FakeOrchestratorWithBugs.calls = []
    monkeypatch.setattr(pr_reviewer, "Orchestrator", lambda model=None: FakeOrchestratorWithBugs())
    provider = FailingInlineProvider()
    reviewer = pr_reviewer.PRReviewer(provider=provider)
    monkeypatch.setattr(reviewer, "_build_impact_report", lambda *args, **kwargs: None)

    comment, url = asyncio.run(reviewer.review_pr("owner/repo", 7, dry_run=False))

    # summary comment 正常发布
    assert url == "https://example.test/comment/1"
    assert "AI Code Review" in comment
    # inline 全部失败但不影响结果
    assert len(provider.inline_suggestions) == 0
