"""
Patch Applier - 代码补丁应用与回滚

设计要点：
- 用 original_snippet 定位，不依赖行号，避免多次迭代后行号漂移
- 从后往前应用 patches（按 start_line 降序），避免前面替换影响后面行号
- 每次应用前备份，失败可回滚
- 应用后做语法检查
"""
from __future__ import annotations

import ast
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from .models import FilePatch


class PatchApplier:
    """代码补丁应用器"""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self._backup_path: Optional[Path] = None

    def backup(self) -> str:
        """备份原始代码，返回备份路径"""
        tmp = tempfile.NamedTemporaryFile(
            suffix=".py.bak", delete=False,
            prefix=f"{self.file_path.stem}_"
        )
        shutil.copy2(str(self.file_path), tmp.name)
        self._backup_path = Path(tmp.name)
        return tmp.name

    def rollback(self) -> bool:
        """从备份恢复原始代码"""
        if self._backup_path and self._backup_path.exists():
            shutil.copy2(str(self._backup_path), str(self.file_path))
            self._backup_path.unlink(missing_ok=True)
            self._backup_path = None
            return True
        return False

    def apply(self, patches: List[FilePatch]) -> Tuple[bool, List[str]]:
        """
        应用补丁列表

        策略：
        1. 按 start_line 降序排列（从后往前替换）
        2. 用 original_snippet 做安全校验
        3. 不匹配则跳过该 patch 并记录警告

        Returns:
            (success, warnings) - success 表示至少应用了一个 patch
        """
        if not patches:
            return False, ["No patches to apply"]

        code = self.file_path.read_text(encoding="utf-8")
        warnings = []
        applied = 0

        # 按 start_line 降序排列，从后往前替换
        sorted_patches = sorted(patches, key=lambda p: p.start_line, reverse=True)

        for patch in sorted_patches:
            original = patch.original_snippet.strip()
            patched = patch.patched_snippet.strip()

            if not original:
                warnings.append(f"Patch at line {patch.start_line}: empty original_snippet, skipped")
                continue

            if original == patched:
                warnings.append(f"Patch at line {patch.start_line}: no change, skipped")
                continue

            # 用 original_snippet 定位（精确匹配）
            if original in code:
                code = code.replace(original, patched, 1)
                applied += 1
            else:
                # 尝试去除首尾空白后匹配
                lines = code.splitlines()
                matched = self._fuzzy_match(lines, original, patch.start_line)
                if matched is not None:
                    start_idx, end_idx = matched
                    indent = self._detect_indent(lines[start_idx])
                    patched_lines = self._apply_indent(patched, indent)
                    lines[start_idx:end_idx + 1] = patched_lines.splitlines()
                    code = "\n".join(lines)
                    if not code.endswith("\n"):
                        code += "\n"
                    applied += 1
                else:
                    warnings.append(
                        f"Patch at line {patch.start_line}: original_snippet not found in file, skipped. "
                        f"Snippet: {original[:80]}..."
                    )

        if applied > 0:
            self.file_path.write_text(code, encoding="utf-8")

        return applied > 0, warnings

    def verify_syntax(self, language: str = "python") -> bool:
        """检查修改后的文件语法是否合法"""
        if language != "python":
            return True  # 非 Python 暂时跳过语法检查

        try:
            code = self.file_path.read_text(encoding="utf-8")
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    def cleanup_backup(self):
        """清理备份文件"""
        if self._backup_path and self._backup_path.exists():
            self._backup_path.unlink(missing_ok=True)
            self._backup_path = None

    def _fuzzy_match(
        self, lines: List[str], snippet: str, hint_line: int
    ) -> Optional[Tuple[int, int]]:
        """
        模糊匹配：在 hint_line 附近查找 snippet
        返回 (start_idx, end_idx) 或 None
        """
        snippet_lines = [l.strip() for l in snippet.splitlines() if l.strip()]
        if not snippet_lines:
            return None

        # 搜索范围：hint_line 前后 10 行
        search_start = max(0, hint_line - 10)
        search_end = min(len(lines), hint_line + 10)

        for i in range(search_start, search_end):
            if lines[i].strip() == snippet_lines[0]:
                # 检查后续行是否匹配
                end_i = i
                matched = True
                for j, sl in enumerate(snippet_lines[1:], 1):
                    if i + j >= len(lines) or lines[i + j].strip() != sl:
                        matched = False
                        break
                    end_i = i + j
                if matched:
                    return (i, end_i)

        return None

    def _detect_indent(self, line: str) -> str:
        """检测行的缩进"""
        return line[: len(line) - len(line.lstrip())]

    def _apply_indent(self, code: str, indent: str) -> str:
        """给代码块应用缩进"""
        lines = code.splitlines()
        if not lines:
            return code

        # 检测代码块自身的最小缩进
        min_indent = float("inf")
        for line in lines:
            if line.strip():
                min_indent = min(min_indent, len(line) - len(line.lstrip()))
        if min_indent == float("inf"):
            min_indent = 0

        result = []
        for line in lines:
            if line.strip():
                result.append(indent + line[min_indent:])
            else:
                result.append("")
        return "\n".join(result)
