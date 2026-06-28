"""
GitHub PR provider - PR 数据获取 + Comment 发布
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from github import Github, GithubException

from integrations.git_provider import (
    CommitMeta,
    InlineSuggestion,
    PRDiff,
    PRInfo,
    PRProvider,
    ProviderComment,
    REVIEW_MARKER_NAME,
)
from tools.repo_index import SourceFile, detect_language as detect_index_language, source_file_from_github_blob


# ── 工具函数 ──────────────────────────────────────────────

_LANGUAGE_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".java": "java",
    ".go": "go", ".rs": "rust", ".cpp": "cpp", ".c": "c",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".kt": "kotlin", ".scala": "scala", ".sh": "bash",
}

# 跳过这些文件（lock 文件、生成文件、二进制等）
_SKIP_PATTERNS = [
    r"package-lock\.json$", r"yarn\.lock$", r"poetry\.lock$",
    r"Pipfile\.lock$", r"\.lock$", r"\.min\.js$", r"\.min\.css$",
    r"dist/", r"build/", r"\.pb\.go$", r"_generated\.",
    r"\.svg$", r"\.png$", r"\.jpg$", r"\.gif$", r"\.ico$",
    r"\.woff", r"\.ttf$", r"\.eot$",
]


def detect_language(filename: str) -> str:
    """根据文件后缀检测编程语言"""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _LANGUAGE_MAP.get(suffix, "unknown")


def should_skip_file(filename: str) -> bool:
    """判断是否应跳过该文件"""
    for pattern in _SKIP_PATTERNS:
        if re.search(pattern, filename):
            return True
    return False


def extract_added_lines(patch: str) -> str:
    """
    从 git diff patch 中提取新增行
    只保留以 + 开头的行（去掉 + 前缀），跳过 +++ 文件头行
    """
    if not patch:
        return ""
    lines = []
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])  # 去掉 + 前缀
    return "\n".join(lines)


# ── GitHub PR Provider ────────────────────────────────────

class GitHubPRProvider(PRProvider):
    """GitHub PR 数据获取和 Comment 发布"""

    def __init__(self, token: str):
        self.github = Github(token)

    def parse_pr_url(self, url: str) -> Tuple[str, int]:
        """
        解析 PR URL，返回 (repo_full_name, pr_number)

        支持格式：
        - https://github.com/owner/repo/pull/123
        - owner/repo#123
        - 123（需要配合 repo_name 使用）
        """
        # https://github.com/owner/repo/pull/123
        m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
        if m:
            return m.group(1), int(m.group(2))

        # owner/repo#123
        m = re.match(r"([^/]+/[^#]+)#(\d+)", url)
        if m:
            return m.group(1), int(m.group(2))

        raise ValueError(
            f"无法解析 PR URL: {url}\n"
            "支持格式: https://github.com/owner/repo/pull/123 或 owner/repo#123"
        )

    def get_pr_info(self, repo_name: str, pr_number: int) -> PRInfo:
        """获取 PR 基本信息和所有文件 diff"""
        try:
            repo = self.github.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
        except GithubException as e:
            raise RuntimeError(f"GitHub API 错误: {e.status} {e.data}") from e

        diffs = []
        for file in pr.get_files():
            diff = self._file_to_pr_diff(repo, file, pr.head.sha)
            if diff:
                diffs.append(diff)

        return PRInfo(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            author=pr.user.login,
            base_branch=pr.base.ref,
            head_branch=pr.head.ref,
            head_sha=pr.head.sha,
            head_repo_full_name=pr.head.repo.full_name if pr.head.repo else repo_name,
            repo_full_name=repo_name,
            html_url=pr.html_url,
            diffs=diffs,
        )

    def get_diff_files(self, repo_name: str, pr_number: int) -> List[PRDiff]:
        """获取 PR 中可用于审查的 diff 文件。"""
        return self.get_pr_info(repo_name, pr_number).diffs

    def find_open_prs_by_head_branch(self, repo_name: str, branch: str) -> List[int]:
        """
        查找同一仓库内 head 分支对应的 open PR 编号。

        GitHub push 事件不直接包含 PR 编号，用这个方法把 push 分支映射回 PR。
        """
        try:
            repo = self.github.get_repo(repo_name)
            pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
            return [pr.number for pr in pulls]
        except GithubException as e:
            raise RuntimeError(f"查找分支 PR 失败: {e.status} {e.data}") from e

    def publish_comment(self, repo_name: str, pr_number: int, body: str) -> str:
        """
        在 PR 下发 Summary Comment
        返回 comment 的 HTML URL
        """
        try:
            repo = self.github.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            comment = pr.create_issue_comment(body)
            return comment.html_url
        except GithubException as e:
            raise RuntimeError(f"发布 Comment 失败: {e.status} {e.data}") from e

    def update_comment(self, repo_name: str, comment_id: int, body: str) -> str:
        """更新已有 PR comment，返回 comment URL。"""
        try:
            repo = self.github.get_repo(repo_name)
            comment = repo.get_issue_comment(comment_id)
            comment.edit(body)
            return comment.html_url
        except GithubException as e:
            raise RuntimeError(f"更新 Comment 失败: {e.status} {e.data}") from e

    def post_review_comment(self, repo_name: str, pr_number: int, body: str) -> str:
        """Backward-compatible alias for older call sites."""
        return self.publish_comment(repo_name, pr_number, body)

    def publish_inline_suggestion(
        self,
        repo_name: str,
        pr_number: int,
        suggestion: InlineSuggestion,
    ) -> str:
        """
        在 PR diff 指定行发布 inline comment。

        GitHub 的 inline comment 需要 commit SHA、文件 path、side 和 line。
        """
        try:
            repo = self.github.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            kwargs = {
                "body": suggestion.body,
                "commit": pr.head.sha,
                "path": suggestion.file_path,
                "line": suggestion.line,
                "side": suggestion.side,
            }
            if suggestion.start_line is not None:
                kwargs["start_line"] = suggestion.start_line
            if suggestion.start_side is not None:
                kwargs["start_side"] = suggestion.start_side

            comment = pr.create_review_comment(**kwargs)
            return comment.html_url
        except GithubException as e:
            raise RuntimeError(f"发布 Inline Suggestion 失败: {e.status} {e.data}") from e

    def publish_labels(self, repo_name: str, pr_number: int, labels: List[str]) -> List[str]:
        """替换 PR 关联 issue 的 labels，并返回最新 label 名称。"""
        try:
            repo = self.github.get_repo(repo_name)
            issue = repo.get_issue(pr_number)
            issue.set_labels(*labels)
            return [label.name for label in issue.get_labels()]
        except GithubException as e:
            raise RuntimeError(f"发布 Labels 失败: {e.status} {e.data}") from e

    def get_previous_review(
        self,
        repo_name: str,
        pr_number: int,
        marker: str = REVIEW_MARKER_NAME,
    ) -> Optional[ProviderComment]:
        """获取最近一条包含 marker 的历史 PR 评论。"""
        try:
            repo = self.github.get_repo(repo_name)
            issue = repo.get_issue(pr_number)
            for comment in reversed(list(issue.get_comments())):
                if marker in (comment.body or ""):
                    return ProviderComment(
                        id=comment.id,
                        body=comment.body or "",
                        html_url=comment.html_url,
                    )
            return None
        except GithubException as e:
            raise RuntimeError(f"获取历史 Review Comment 失败: {e.status} {e.data}") from e

    def get_incremental_diff_files(
        self,
        repo_name: str,
        pr_number: int,
        base_sha: str,
        head_sha: str,
    ) -> List[PRDiff]:
        """通过 GitHub compare API 获取 base_sha...head_sha 的变更文件。"""
        try:
            repo = self.github.get_repo(repo_name)
            comparison = repo.compare(base_sha, head_sha)
            diffs = []
            for file in comparison.files:
                diff = self._file_to_pr_diff(repo, file, head_sha)
                if diff:
                    diffs.append(diff)
            return diffs
        except GithubException as e:
            raise RuntimeError(f"获取增量 Diff 失败: {e.status} {e.data}") from e

    def get_commit_range(
        self,
        repo_name: str,
        base_sha: str,
        head_sha: str,
        skip_merge_commits: bool = True,
    ) -> List[CommitMeta]:
        """获取 base_sha...head_sha 之间的 commit 列表。"""
        try:
            repo = self.github.get_repo(repo_name)
            comparison = repo.compare(base_sha, head_sha)
            commits = []
            for c in comparison.commits:
                is_merge = len(c.parents) > 1
                if skip_merge_commits and is_merge:
                    continue
                commits.append(CommitMeta(
                    sha=c.sha,
                    short_sha=c.sha[:8],
                    message=c.commit.message.split("\n")[0],
                    author=c.author.login if c.author else c.commit.author.name,
                    is_merge=is_merge,
                ))
            return commits
        except GithubException as e:
            raise RuntimeError(f"获取 commit 范围失败: {e.status} {e.data}") from e

    def get_repo_source_snapshot(
        self,
        repo_name: str,
        ref: str,
        max_files: int = 500,
        max_file_bytes: int = 250_000,
    ) -> List[SourceFile]:
        """
        获取仓库某个 ref 下的可解析源码快照，用于 repo-level symbol index。

        通过 Git tree 递归枚举文件，再按需拉取源码 blob。限制文件数量和大小，
        避免 webhook 审查在大仓库上拉取过多内容。
        """
        try:
            repo = self.github.get_repo(repo_name)
            tree = repo.get_git_tree(ref, recursive=True).tree
            source_files: List[SourceFile] = []

            for item in tree:
                if len(source_files) >= max_files:
                    break
                if item.type != "blob":
                    continue
                language = detect_index_language(item.path)
                if not language:
                    continue
                if item.size and item.size > max_file_bytes:
                    continue

                blob = repo.get_git_blob(item.sha)
                source = source_file_from_github_blob(item.path, blob.content, blob.encoding)
                if source:
                    source_files.append(source)

            return source_files
        except GithubException as e:
            raise RuntimeError(f"获取仓库源码快照失败: {e.status} {e.data}") from e

    def _file_to_pr_diff(self, repo, file, ref: str) -> Optional[PRDiff]:
        if file.status == "removed":
            return None
        if should_skip_file(file.filename):
            return None

        full_content = ""
        try:
            content_file = repo.get_contents(file.filename, ref=ref)
            if isinstance(content_file, list):
                content_file = content_file[0]
            full_content = content_file.decoded_content.decode("utf-8", errors="replace")
        except Exception:
            full_content = extract_added_lines(file.patch or "")

        added = extract_added_lines(file.patch or "")
        if not added.strip():
            return None

        return PRDiff(
            file_path=file.filename,
            language=detect_language(file.filename),
            patch=file.patch or "",
            added_lines=added,
            full_content=full_content,
            status=file.status,
        )


GitHubPRClient = GitHubPRProvider
