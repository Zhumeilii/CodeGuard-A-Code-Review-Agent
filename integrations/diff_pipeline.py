"""
Token-aware diff processing for PR review.

The pipeline converts provider-specific PR diffs into review chunks that are
safe to send to agents:
- skip generated/binary/lock files
- parse and annotate hunks with old/new line numbers
- add bounded new-file context around changed lines
- estimate token cost
- compress oversized patches
- split large files into multiple chunks
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from integrations.git_provider import PRDiff


_DEFAULT_IGNORE_PATTERNS = [
    r"package-lock\.json$",
    r"yarn\.lock$",
    r"poetry\.lock$",
    r"Pipfile\.lock$",
    r"\.lock$",
    r"\.min\.js$",
    r"\.min\.css$",
    r"(^|/)dist/",
    r"(^|/)build/",
    r"\.pb\.go$",
    r"_generated\.",
    r"\.svg$",
    r"\.png$",
    r"\.jpg$",
    r"\.jpeg$",
    r"\.gif$",
    r"\.ico$",
    r"\.woff2?$",
    r"\.ttf$",
    r"\.eot$",
]

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$"
)
_TOKEN_RE = re.compile(r"\w+|[^\s\w]", re.UNICODE)

# ── 模型上下文窗口映射 ────────────────────────────────────
_MODEL_CONTEXT_WINDOWS = {
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-haiku-4-5": 200000,
    "gpt-4": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "qwen-plus": 128000,
    "qwen-turbo": 128000,
    "qwen-max": 128000,
    "deepseek-chat": 128000,
    "deepseek-coder": 128000,
}

# 预留空间常量
_SYSTEM_PROMPT_RESERVE = 1500    # 系统 prompt + agent 指令
_CONTEXT_RESERVE = 4000          # RAG 上下文、repo context、PR 元数据
_RESPONSE_RESERVE = 6000         # 模型输出空间
_DEFAULT_CONTEXT_WINDOW = 128000  # 未知模型的默认上下文窗口


def _lookup_context_window(model_id: str) -> int:
    """查找模型上下文窗口大小，支持精确/前缀/关键词匹配"""
    if not model_id:
        env_window = os.getenv("MODEL_CONTEXT_WINDOW")
        return int(env_window) if env_window else _DEFAULT_CONTEXT_WINDOW

    model_lower = model_id.lower().strip()

    # 精确匹配
    if model_lower in _MODEL_CONTEXT_WINDOWS:
        return _MODEL_CONTEXT_WINDOWS[model_lower]

    # 前缀匹配
    for key, value in _MODEL_CONTEXT_WINDOWS.items():
        if model_lower.startswith(key) or key.startswith(model_lower):
            return value

    # 关键词匹配
    if "claude" in model_lower:
        return 200000
    if "gpt-4" in model_lower:
        return 128000
    if "qwen" in model_lower:
        return 128000
    if "deepseek" in model_lower:
        return 128000

    # 环境变量覆盖
    env_window = os.getenv("MODEL_CONTEXT_WINDOW")
    if env_window:
        return int(env_window)

    return _DEFAULT_CONTEXT_WINDOW


@dataclass(frozen=True)
class DiffPipelineConfig:
    max_tokens_per_chunk: int = int(os.getenv("DIFF_MAX_TOKENS_PER_CHUNK", "6000"))
    max_chunks: int = int(os.getenv("DIFF_MAX_CHUNKS", "24"))
    context_lines: int = int(os.getenv("DIFF_CONTEXT_LINES", "6"))
    min_file_tokens: int = 256
    ignore_patterns: Sequence[str] = field(default_factory=lambda: tuple(_DEFAULT_IGNORE_PATTERNS))

    @classmethod
    def from_model(cls, model_id: str = None) -> "DiffPipelineConfig":
        """根据模型上下文窗口动态计算 chunk 预算。

        公式: max_chunks = (context_window - reserves) / max_tokens_per_chunk
        环境变量 DIFF_MAX_CHUNKS 显式设置时优先使用其值。
        """
        model_id = model_id or os.getenv("LLM_MODEL_ID") or os.getenv("MODEL", "")
        context_window = _lookup_context_window(model_id)

        available = context_window - _SYSTEM_PROMPT_RESERVE - _CONTEXT_RESERVE - _RESPONSE_RESERVE
        max_tokens_per_chunk = int(os.getenv("DIFF_MAX_TOKENS_PER_CHUNK", "6000"))
        dynamic_max_chunks = max(available // max_tokens_per_chunk, 4)

        # 环境变量显式设置时优先
        env_max_chunks = os.getenv("DIFF_MAX_CHUNKS")
        if env_max_chunks:
            dynamic_max_chunks = int(env_max_chunks)

        return cls(
            max_tokens_per_chunk=max_tokens_per_chunk,
            max_chunks=dynamic_max_chunks,
        )


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: List[str]
    changed_new_lines: List[int]


@dataclass
class DiffFile:
    file_path: str
    language: str
    status: str
    patch: str
    full_content: str
    hunks: List[DiffHunk]
    token_count: int = 0
    skipped_reason: Optional[str] = None

    @property
    def changed_lines(self) -> List[int]:
        lines = []
        for hunk in self.hunks:
            lines.extend(hunk.changed_new_lines)
        return lines


@dataclass
class ReviewChunk:
    chunk_id: str
    file_path: str
    language: str
    content: str
    token_count: int
    source_files: List[str]
    omitted_files: List[str] = field(default_factory=list)
    compressed: bool = False


@dataclass
class DiffPipelineResult:
    files: List[DiffFile]
    chunks: List[ReviewChunk]
    skipped_files: List[DiffFile]
    omitted_files: List[str]
    total_tokens: int


def estimate_tokens(text: str) -> int:
    """Fast local token estimate; avoids adding a tokenizer dependency."""
    if not text:
        return 0
    return len(_TOKEN_RE.findall(text))


def should_ignore_file(file_path: str, patterns: Sequence[str]) -> Optional[str]:
    for pattern in patterns:
        if re.search(pattern, file_path):
            return pattern
    return None


def build_token_aware_diff(
    diffs: Sequence[PRDiff],
    config: Optional[DiffPipelineConfig] = None,
) -> DiffPipelineResult:
    """Build annotated, token-bounded review chunks from provider diffs."""
    config = config or DiffPipelineConfig()
    files = []
    skipped_files = []

    for diff in diffs:
        ignored_by = should_ignore_file(diff.file_path, config.ignore_patterns)
        if ignored_by:
            skipped_files.append(_skipped_file(diff, f"ignored by pattern: {ignored_by}"))
            continue
        if diff.status == "removed":
            skipped_files.append(_skipped_file(diff, "removed file"))
            continue
        if not (diff.patch or "").strip():
            skipped_files.append(_skipped_file(diff, "empty patch"))
            continue

        hunks = parse_patch_hunks(diff.patch)
        if not hunks:
            skipped_files.append(_skipped_file(diff, "no parseable hunks"))
            continue
        if not any(hunk.changed_new_lines for hunk in hunks):
            skipped_files.append(_skipped_file(diff, "no added or modified lines"))
            continue

        file = DiffFile(
            file_path=diff.file_path,
            language=diff.language,
            status=diff.status,
            patch=diff.patch or "",
            full_content=diff.full_content or "",
            hunks=hunks,
        )
        rendered = render_diff_file(file, config)
        file.token_count = estimate_tokens(rendered)
        files.append(file)

    chunks, omitted_files = _build_review_chunks(files, config)
    return DiffPipelineResult(
        files=files,
        chunks=chunks,
        skipped_files=skipped_files,
        omitted_files=omitted_files,
        total_tokens=sum(chunk.token_count for chunk in chunks),
    )


def parse_patch_hunks(patch: str) -> List[DiffHunk]:
    """Parse unified diff hunks from a provider patch string."""
    hunks: List[DiffHunk] = []
    current_header = None
    current_lines: List[str] = []
    old_start = old_count = new_start = new_count = 0

    def flush_current():
        if current_header is None:
            return
        hunks.append(
            _build_hunk(
                header=current_header,
                lines=list(current_lines),
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
        )

    for line in patch.splitlines():
        match = _HUNK_RE.match(line)
        if match:
            flush_current()
            current_header = line
            current_lines = []
            old_start = int(match.group("old_start"))
            old_count = int(match.group("old_count") or "1")
            new_start = int(match.group("new_start"))
            new_count = int(match.group("new_count") or "1")
            continue
        if current_header is not None:
            current_lines.append(line)

    flush_current()
    return hunks


def render_diff_file(
    file: DiffFile,
    config: Optional[DiffPipelineConfig] = None,
    hunks: Optional[Sequence[DiffHunk]] = None,
) -> str:
    """Render one diff file as an annotated prompt payload."""
    config = config or DiffPipelineConfig()
    selected_hunks = list(hunks if hunks is not None else file.hunks)
    lines = [
        f"## File: {file.file_path}",
        f"Language: {file.language}",
        f"Status: {file.status}",
        "",
        "Annotated unified diff:",
        "```diff",
    ]

    for hunk in selected_hunks:
        lines.extend(_render_hunk(file, hunk, config.context_lines))

    lines.extend(["```", ""])
    return "\n".join(lines).strip()


def _build_review_chunks(
    files: Sequence[DiffFile],
    config: DiffPipelineConfig,
) -> tuple[List[ReviewChunk], List[str]]:
    chunks: List[ReviewChunk] = []
    omitted_files: List[str] = []

    for file in files:
        if len(chunks) >= config.max_chunks:
            omitted_files.append(file.file_path)
            continue

        file_chunks = _chunks_for_file(file, config)
        remaining_slots = config.max_chunks - len(chunks)
        chunks.extend(file_chunks[:remaining_slots])
        if len(file_chunks) > remaining_slots:
            omitted_files.append(file.file_path)

    if omitted_files and chunks:
        chunks[-1].omitted_files.extend(omitted_files)
        chunks[-1].content += (
            "\n\nAdditional files omitted because the diff exceeded the configured chunk budget:\n"
            + "\n".join(f"- {path}" for path in omitted_files)
        )
        chunks[-1].token_count = estimate_tokens(chunks[-1].content)

    return chunks, omitted_files


def _chunks_for_file(file: DiffFile, config: DiffPipelineConfig) -> List[ReviewChunk]:
    max_tokens = max(config.max_tokens_per_chunk, config.min_file_tokens)
    chunks = []
    current_hunks: List[DiffHunk] = []
    chunk_index = 1

    for hunk in file.hunks:
        trial_hunks = current_hunks + [hunk]
        trial_content = render_diff_file(file, config, trial_hunks)
        trial_tokens = estimate_tokens(trial_content)

        if current_hunks and trial_tokens > max_tokens:
            chunks.append(_chunk_from_hunks(file, current_hunks, config, chunk_index, compressed=False))
            chunk_index += 1
            current_hunks = [hunk]
        else:
            current_hunks = trial_hunks

        single_content = render_diff_file(file, config, [hunk])
        if estimate_tokens(single_content) > max_tokens:
            if current_hunks == [hunk]:
                current_hunks = []
            split_chunks = _split_large_hunk(file, hunk, config, chunk_index)
            chunks.extend(split_chunks)
            chunk_index += len(split_chunks)

    if current_hunks:
        chunks.append(_chunk_from_hunks(file, current_hunks, config, chunk_index, compressed=False))

    return chunks


def _chunk_from_hunks(
    file: DiffFile,
    hunks: Sequence[DiffHunk],
    config: DiffPipelineConfig,
    chunk_index: int,
    compressed: bool,
) -> ReviewChunk:
    content = render_diff_file(file, config, hunks)
    total_chunks_hint = "" if chunk_index == 1 else f"#part-{chunk_index}"
    return ReviewChunk(
        chunk_id=f"{file.file_path}:{chunk_index}",
        file_path=f"{file.file_path}{total_chunks_hint}",
        language=file.language,
        content=content,
        token_count=estimate_tokens(content),
        source_files=[file.file_path],
        compressed=compressed,
    )


def _split_large_hunk(
    file: DiffFile,
    hunk: DiffHunk,
    config: DiffPipelineConfig,
    start_index: int,
) -> List[ReviewChunk]:
    """Split an oversized hunk by annotated diff lines."""
    rendered_lines = _render_hunk(file, hunk, config.context_lines)
    header = [
        f"## File: {file.file_path}",
        f"Language: {file.language}",
        f"Status: {file.status}",
        "",
        "Annotated unified diff:",
        "```diff",
    ]
    footer = ["```", ""]
    chunks = []
    current = []
    chunk_index = start_index
    max_tokens = max(config.max_tokens_per_chunk, config.min_file_tokens)

    for line in rendered_lines:
        trial = "\n".join(header + current + [line] + footer)
        if current and estimate_tokens(trial) > max_tokens:
            content = "\n".join(header + current + footer).strip()
            chunks.append(
                ReviewChunk(
                    chunk_id=f"{file.file_path}:{chunk_index}",
                    file_path=f"{file.file_path}#part-{chunk_index}",
                    language=file.language,
                    content=content,
                    token_count=estimate_tokens(content),
                    source_files=[file.file_path],
                    compressed=True,
                )
            )
            chunk_index += 1
            current = [line]
        else:
            current.append(line)

    if current:
        content = "\n".join(header + current + footer).strip()
        chunks.append(
            ReviewChunk(
                chunk_id=f"{file.file_path}:{chunk_index}",
                file_path=f"{file.file_path}#part-{chunk_index}",
                language=file.language,
                content=content,
                token_count=estimate_tokens(content),
                source_files=[file.file_path],
                compressed=True,
            )
        )

    return chunks


def _build_hunk(
    header: str,
    lines: List[str],
    old_start: int,
    old_count: int,
    new_start: int,
    new_count: int,
) -> DiffHunk:
    changed_new_lines = []
    old_line = old_start
    new_line = new_start

    for line in lines:
        if line.startswith("\\"):
            continue
        prefix = line[:1]
        if prefix == "+":
            changed_new_lines.append(new_line)
            new_line += 1
        elif prefix == "-":
            old_line += 1
        else:
            old_line += 1
            new_line += 1

    return DiffHunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        header=header,
        lines=lines,
        changed_new_lines=changed_new_lines,
    )


def _render_hunk(file: DiffFile, hunk: DiffHunk, context_lines: int) -> List[str]:
    """
    将diff hunk渲染为可读的markdown格式
    """
    lines = [hunk.header, "# columns: old_line new_line diff_line"]
    old_line = hunk.old_start
    new_line = hunk.new_start

    for raw in hunk.lines:
        if raw.startswith("\\"):
            lines.append(f"      {'':>5} {raw}")
            continue
        prefix = raw[:1] if raw else " "
        body = raw[1:] if raw and prefix in {" ", "+", "-"} else raw
        if prefix == "+":
            lines.append(f"{'':>5} {new_line:>5} +{body}")
            new_line += 1
        elif prefix == "-":
            lines.append(f"{old_line:>5} {'':>5} -{body}")
            old_line += 1
        else:
            lines.append(f"{old_line:>5} {new_line:>5}  {body}")
            old_line += 1
            new_line += 1

    extension = _render_new_file_context(file.full_content, hunk.changed_new_lines, context_lines)
    if extension:
        lines.extend(["", "# Extended new-file context around changed lines:"])
        lines.extend(extension)

    lines.append("")
    return lines


def _render_new_file_context(
    full_content: str,
    changed_new_lines: Sequence[int],
    context_lines: int,
) -> List[str]:
    """Render new-file context around changed lines."""
    if not full_content or not changed_new_lines or context_lines <= 0:
        return []

    content_lines = full_content.splitlines()
    if not content_lines:
        return []

    start = max(min(changed_new_lines) - context_lines, 1)
    end = min(max(changed_new_lines) + context_lines, len(content_lines))
    return [
        f"{line_no:>5}: {content_lines[line_no - 1]}"
        for line_no in range(start, end + 1)
    ]


def _skipped_file(diff: PRDiff, reason: str) -> DiffFile:
    return DiffFile(
        file_path=diff.file_path,
        language=diff.language,
        status=diff.status,
        patch=diff.patch or "",
        full_content=diff.full_content or "",
        hunks=[],
        skipped_reason=reason,
    )
