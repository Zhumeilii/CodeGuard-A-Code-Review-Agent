"""Resolve review finding locations from exact code snippets.

The resolver mirrors Open Code Review's positioning contract: agents should
return an ``existing_code`` snippet copied from the changed code, and the
deterministic layer maps that snippet back to concrete PR line numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from integrations.diff_pipeline import DiffFile, DiffHunk, parse_patch_hunks


@dataclass(frozen=True)
class LineResolution:
    """Resolved location for an ``existing_code`` snippet."""

    start_line: int
    end_line: int
    source: str

    @property
    def line(self) -> int:
        return self.end_line


@dataclass(frozen=True)
class _IndexedLine:
    line_num: int
    content: str


def resolve_existing_code(
    existing_code: str,
    diff_file: Optional[DiffFile] = None,
    *,
    patch: str = "",
    full_content: str = "",
) -> Optional[LineResolution]:
    """Resolve a copied code snippet to new-file line numbers.

    Matching is intentionally exact after light normalization: whitespace at
    line boundaries and leading diff markers are ignored, but internal content
    must remain the same. The diff new-side is preferred so inline comments stay
    anchored to changed lines; full-content matching is only a fallback.
    """
    target_lines = _split_and_normalize(existing_code)
    if not target_lines:
        return None

    if diff_file is not None:
        hunks = diff_file.hunks or parse_patch_hunks(diff_file.patch or "")
        full_content = diff_file.full_content or full_content
    else:
        hunks = parse_patch_hunks(patch or "")

    resolved = _resolve_from_hunks(target_lines, hunks)
    if resolved:
        return resolved

    return _resolve_from_full_content(target_lines, full_content)


def resolve_issue_location(issue: dict, diff_file: Optional[DiffFile]) -> dict:
    """Return a copy of ``issue`` with line/start_line/end_line resolved.

    ``existing_code`` is the authoritative locator. Existing ``line`` values are
    preserved as a fallback for older agent responses.
    """
    resolved_issue = dict(issue)
    existing_code = resolved_issue.get("existing_code") or resolved_issue.get("evidence") or ""
    resolution = resolve_existing_code(existing_code, diff_file) if existing_code else None
    if resolution:
        resolved_issue["start_line"] = resolution.start_line
        resolved_issue["end_line"] = resolution.end_line
        resolved_issue["line"] = resolution.line
        resolved_issue["line_resolution"] = resolution.source
    return resolved_issue


def _resolve_from_hunks(
    target_lines: Sequence[str],
    hunks: Iterable[DiffHunk],
) -> Optional[LineResolution]:
    for hunk in hunks:
        new_side = _extract_new_side_lines(hunk)
        match = _match_consecutive(new_side, target_lines)
        if match:
            start_line, end_line = match
            return LineResolution(start_line=start_line, end_line=end_line, source="diff_hunk")
    return None


def _extract_new_side_lines(hunk: DiffHunk) -> List[_IndexedLine]:
    result: List[_IndexedLine] = []
    new_line = hunk.new_start
    for raw_line in hunk.lines:
        if raw_line.startswith("\\"):
            continue
        if raw_line.startswith("-"):
            continue
        content = raw_line[1:] if raw_line.startswith(("+", " ")) else raw_line
        result.append(_IndexedLine(new_line, _normalize_line(content)))
        new_line += 1
    return result


def _resolve_from_full_content(
    target_lines: Sequence[str],
    full_content: str,
) -> Optional[LineResolution]:
    if not full_content:
        return None
    indexed = [
        _IndexedLine(index + 1, _normalize_line(line))
        for index, line in enumerate(full_content.splitlines())
    ]
    match = _match_consecutive(indexed, target_lines)
    if not match:
        return None
    start_line, end_line = match
    return LineResolution(start_line=start_line, end_line=end_line, source="full_content")


def _match_consecutive(
    indexed_lines: Sequence[_IndexedLine],
    target_lines: Sequence[str],
) -> Optional[Tuple[int, int]]:
    if not target_lines or len(indexed_lines) < len(target_lines):
        return None
    for start in range(0, len(indexed_lines) - len(target_lines) + 1):
        window = indexed_lines[start : start + len(target_lines)]
        if all(line.content == target for line, target in zip(window, target_lines)):
            return window[0].line_num, window[-1].line_num
    return None


def _split_and_normalize(code: str) -> List[str]:
    return [
        normalized
        for normalized in (_normalize_line(line) for line in (code or "").splitlines())
        if normalized
    ]


def _normalize_line(line: str) -> str:
    stripped = (line or "").strip()
    if stripped.startswith(("+", "-")):
        stripped = stripped[1:].strip()
    return stripped
