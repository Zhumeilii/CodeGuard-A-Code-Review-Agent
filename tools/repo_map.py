"""
Repo Map - 基于 tree-sitter 的代码调用图构建器

用途：
- 扫描代码库，提取函数定义和调用关系
- 为 FixAgent 提供影响范围分析
- 避免修复某函数时破坏其调用方

降级策略：tree-sitter 不可用时自动切换到 ast 模块
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class FunctionNode:
    """函数节点"""
    name: str
    file_path: str
    start_line: int
    end_line: int
    calls: Set[str] = field(default_factory=set)      # 此函数调用了哪些函数
    called_by: Set[str] = field(default_factory=set)  # 哪些函数调用了此函数


class RepoMap:
    """代码库调用图"""

    def __init__(self, repo_root: str, language: str = "python"):
        self.repo_root = Path(repo_root)
        self.language = language
        self._nodes: Dict[str, FunctionNode] = {}
        self._parser = None
        self._ts_language = None
        self._treesitter_available = False

    def _init_parser(self):
        """延迟初始化 tree-sitter parser"""
        try:
            import tree_sitter_python as tspython
            from tree_sitter import Language, Parser
            self._ts_language = Language(tspython.language())
            self._parser = Parser(self._ts_language)
            self._treesitter_available = True
        except (ImportError, Exception):
            self._treesitter_available = False

    def build(self) -> "RepoMap":
        """扫描 repo_root 下所有源文件，构建调用图"""
        self._init_parser()
        suffix_map = {
            "python": ".py",
            "javascript": ".js",
            "typescript": ".ts",
        }
        suffix = suffix_map.get(self.language, f".{self.language}")

        for file_path in self.repo_root.rglob(f"*{suffix}"):
            if any(skip in str(file_path) for skip in ["__pycache__", ".git", "node_modules", ".venv", "venv"]):
                continue
            self._process_file(file_path)

        self._resolve_called_by()
        return self

    def build_for_file(self, file_path: str) -> "RepoMap":
        """只为单个文件构建调用图（快速模式）"""
        self._init_parser()
        self._process_file(Path(file_path))
        self._resolve_called_by()
        return self

    def _process_file(self, file_path: Path):
        """解析单个文件，提取函数定义和调用关系"""
        try:
            code_bytes = file_path.read_bytes()
        except (IOError, PermissionError):
            return

        if self._treesitter_available:
            self._process_with_treesitter(file_path, code_bytes)
        else:
            self._process_with_ast(file_path, code_bytes.decode("utf-8", errors="ignore"))

    def _process_with_treesitter(self, file_path: Path, code_bytes: bytes):
        """tree-sitter 解析（精确）"""
        try:
            tree = self._parser.parse(code_bytes)
            root = tree.root_node

            # Query 1: 找函数定义
            func_def_query = self._ts_language.query(
                "(function_definition name: (identifier) @name) @def"
            )
            captures = func_def_query.captures(root)

            name_nodes = captures.get("name", [])
            def_nodes = captures.get("def", [])

            for name_node, def_node in zip(name_nodes, def_nodes):
                func_name = code_bytes[name_node.start_byte:name_node.end_byte].decode()
                node = FunctionNode(
                    name=func_name,
                    file_path=str(file_path),
                    start_line=def_node.start_point[0] + 1,
                    end_line=def_node.end_point[0] + 1,
                )

                # Query 2: 在函数体内找调用
                call_query = self._ts_language.query(
                    """[
                      (call function: (identifier) @callee)
                      (call function: (attribute attribute: (identifier) @callee))
                    ]"""
                )
                call_captures = call_query.captures(def_node)
                for callee_node in call_captures.get("callee", []):
                    callee_name = code_bytes[callee_node.start_byte:callee_node.end_byte].decode()
                    node.calls.add(callee_name)

                self._nodes[func_name] = node
        except Exception:
            # tree-sitter 解析失败时降级
            self._process_with_ast(file_path, code_bytes.decode("utf-8", errors="ignore"))

    def _process_with_ast(self, file_path: Path, code: str):
        """ast 模块降级解析"""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_node = FunctionNode(
                    name=node.name,
                    file_path=str(file_path),
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name):
                            func_node.calls.add(child.func.id)
                        elif isinstance(child.func, ast.Attribute):
                            func_node.calls.add(child.func.attr)
                self._nodes[node.name] = func_node

    def _resolve_called_by(self):
        """根据 calls 关系反向填充 called_by"""
        for caller_name, caller_node in self._nodes.items():
            for callee_name in caller_node.calls:
                if callee_name in self._nodes:
                    self._nodes[callee_name].called_by.add(caller_name)

    def get_impact_summary(self, func_names: List[str]) -> str:
        """
        给定一组将被修改的函数名，返回影响范围的自然语言摘要
        供 FixAgent prompt 使用
        """
        lines = []
        for name in func_names:
            if name not in self._nodes:
                continue
            node = self._nodes[name]
            callers = node.called_by
            if callers:
                caller_parts = []
                for c in sorted(callers):
                    if c in self._nodes:
                        cn = self._nodes[c]
                        caller_parts.append(f"`{c}` ({Path(cn.file_path).name}:{cn.start_line})")
                    else:
                        caller_parts.append(f"`{c}`")
                lines.append(
                    f"- `{name}` ({Path(node.file_path).name}:{node.start_line}) "
                    f"is called by: {', '.join(caller_parts)}"
                )
            else:
                lines.append(
                    f"- `{name}` ({Path(node.file_path).name}:{node.start_line}) "
                    f"has no known callers in this repo"
                )

        if not lines:
            return "No call graph information available for the modified functions."

        header = "## Impact Analysis (Repo Map)\n"
        header += "The following functions will be modified. Their callers are listed below:\n\n"
        return header + "\n".join(lines)

    def get_functions_in_file(self, file_path: str) -> List[FunctionNode]:
        """返回某文件中所有已解析的函数节点"""
        return [n for n in self._nodes.values() if n.file_path == file_path]

    def get_node(self, func_name: str) -> Optional[FunctionNode]:
        """按函数名查找节点"""
        return self._nodes.get(func_name)

    @property
    def using_treesitter(self) -> bool:
        return self._treesitter_available
