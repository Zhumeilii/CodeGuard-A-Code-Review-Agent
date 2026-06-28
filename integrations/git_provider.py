"""
Git provider abstraction for PR automation.

Review logic should depend on this module instead of a concrete platform
client. Platform integrations such as GitHub, GitLab, and local git implement
this interface.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Tuple

from tools.repo_index import SourceFile


REVIEW_MARKER_NAME = "code-review-agent:review"
REVIEW_MARKER_PREFIX = f"<!-- {REVIEW_MARKER_NAME}"
REVIEW_MARKER_RE = re.compile(r"<!--\s*code-review-agent:review(?P<body>.*?)-->", re.DOTALL)


@dataclass
class PRDiff:
    file_path: str
    language: str
    patch: str
    added_lines: str
    full_content: str
    status: str


@dataclass
class PRInfo:
    number: int
    title: str
    body: str
    author: str
    base_branch: str
    head_branch: str
    repo_full_name: str
    html_url: str
    head_sha: str = ""
    head_repo_full_name: str = ""
    diffs: List[PRDiff] = field(default_factory=list)


@dataclass
class CommitMeta:
    """单个 commit 的元数据"""
    sha: str
    short_sha: str
    message: str
    author: str
    is_merge: bool = False


@dataclass
class IncrementalReviewInfo:
    """增量审查范围元数据"""
    last_reviewed_sha: str
    current_head_sha: str
    commits_range: List[CommitMeta] = field(default_factory=list)
    first_new_commit_sha: str = ""
    skipped_merge_commits: int = 0


class IncrementalReviewConfig:
    """增量审查触发条件配置（每次实例化时从环境变量读取最新值）"""

    def __init__(self):
        # 触发增量审查的最少新 commit 数（0 = 不限制）
        self.minimal_commits: int = int(os.getenv("INCREMENTAL_MIN_COMMITS", "0"))
        # 触发增量审查的最少分钟数（距上次审查，0 = 不限制）
        self.minimal_minutes: int = int(os.getenv("INCREMENTAL_MIN_MINUTES", "0"))
        # True = 所有条件都满足才触发（AND），False = 任一条件满足即触发（OR）
        self.require_all_thresholds: bool = (
            os.getenv("INCREMENTAL_REQUIRE_ALL_THRESHOLDS", "false").lower() == "true"
        )
        # 是否跳过 merge commit
        self.skip_merge_commits: bool = (
            os.getenv("INCREMENTAL_SKIP_MERGE_COMMITS", "true").lower() == "true"
        )


@dataclass
class ProviderComment:
    id: int
    body: str
    html_url: str

    @property
    def review_marker(self) -> Dict[str, str]:
        return parse_review_marker(self.body)

    @property
    def reviewed_head_sha(self) -> Optional[str]:
        return self.review_marker.get("head")


def parse_review_marker(body: str) -> Dict[str, str]:
    """Parse the persistent review marker from a comment body."""
    match = REVIEW_MARKER_RE.search(body or "")
    if not match:
        return {}
    marker_body = match.group("body").strip()
    data = {}
    for token in marker_body.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        data[key.strip()] = value.strip().strip('"')
    return data


def build_review_marker(
    *,
    repo_name: str,
    pr_number: int,
    head_sha: str,
    mode: str,
    base_sha: Optional[str] = None,
) -> str:
    parts = [
        REVIEW_MARKER_NAME,
        f"repo={repo_name}",
        f"pr={pr_number}",
        f"head={head_sha or 'unknown'}",
        f"mode={mode}",
    ]
    if base_sha:
        parts.append(f"base={base_sha}")
    return "<!-- " + " ".join(parts) + " -->"


def append_review_marker(body: str, marker: str) -> str:
    """Replace an existing review marker or append a new one."""
    clean_body = REVIEW_MARKER_RE.sub("", body or "").rstrip()
    return f"{clean_body}\n\n{marker}".strip()


@dataclass
class InlineSuggestion:
    file_path: str
    line: int
    body: str
    side: str = "RIGHT"
    start_line: Optional[int] = None
    start_side: Optional[str] = None


class PRProvider(ABC):
    """Common interface for pull request providers."""

    @abstractmethod
    def parse_pr_url(self, url: str) -> Tuple[str, int]:
        """Parse a provider-specific PR URL into (repo_name, pr_number)."""

    @abstractmethod
    def get_pr_info(self, repo_name: str, pr_number: int) -> PRInfo:
        """Return PR metadata. Implementations may include diff files."""

    @abstractmethod
    def get_diff_files(self, repo_name: str, pr_number: int) -> List[PRDiff]:
        """Return files changed in the PR."""

    @abstractmethod
    def publish_comment(self, repo_name: str, pr_number: int, body: str) -> str:
        """Publish a PR-level comment and return its URL."""

    @abstractmethod
    def update_comment(self, repo_name: str, comment_id: int, body: str) -> str:
        """Update an existing PR-level comment and return its URL."""

    @abstractmethod
    def publish_inline_suggestion(
        self,
        repo_name: str,
        pr_number: int,
        suggestion: InlineSuggestion,
    ) -> str:
        """Publish an inline code suggestion/comment and return its URL."""

    @abstractmethod
    def publish_labels(self, repo_name: str, pr_number: int, labels: List[str]) -> List[str]:
        """Replace or publish PR labels and return the resulting label names."""

    @abstractmethod
    def get_previous_review(
        self,
        repo_name: str,
        pr_number: int,
        marker: str = REVIEW_MARKER_NAME,
    ) -> Optional[ProviderComment]:
        """Return the latest prior review comment matching marker, if any."""

    @abstractmethod
    def get_incremental_diff_files(
        self,
        repo_name: str,
        pr_number: int,
        base_sha: str,
        head_sha: str,
    ) -> List[PRDiff]:
        """Return changed files between two commits for an incremental review."""

    def get_commit_range(
        self,
        repo_name: str,
        base_sha: str,
        head_sha: str,
        skip_merge_commits: bool = True,
    ) -> List[CommitMeta]:
        """Optional: 获取两个 SHA 之间的 commit 列表。"""
        return []

    def find_open_prs_by_head_branch(self, repo_name: str, branch: str) -> List[int]:
        """Optional provider hook for mapping push events back to open PRs."""
        raise NotImplementedError("Provider does not support branch-to-PR lookup")

    def get_repo_source_snapshot(
        self,
        repo_name: str,
        ref: str,
        max_files: int = 500,
        max_file_bytes: int = 250_000,
    ) -> List[SourceFile]:
        """Optional provider hook for repo-level impact analysis."""
        return []
