"""
Repo-level symbol index and impact analysis.

This module builds a lightweight repository index from source files at a
specific revision. It prefers structural parsing where available and falls back
to Python's stdlib AST or conservative text heuristics.
"""
from __future__ import annotations

import ast
import base64
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
}

_SKIP_PATH_PARTS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
}

_RISK_KEYWORDS = {
    "auth",
    "login",
    "password",
    "token",
    "secret",
    "permission",
    "payment",
    "billing",
    "delete",
    "admin",
    "sql",
    "query",
    "execute",
}

_JS_TS_LANGUAGES = {"javascript", "typescript"}
_JS_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
_CONFIG_FILENAMES = {"tsconfig.json", "jsconfig.json"}


@dataclass
class SourceFile:
    """A source file snapshot used to build repo context."""

    path: str
    content: str
    language: str


@dataclass
class Symbol:
    """Function/class level symbol extracted from a source file."""

    id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    calls: Set[str] = field(default_factory=set)
    dependencies: Set[str] = field(default_factory=set)

    def overlaps_lines(self, lines: Set[int]) -> bool:
        return bool(lines) and any(self.start_line <= line <= self.end_line for line in lines)


@dataclass(frozen=True)
class ImportBinding:
    local_name: str
    imported_name: Optional[str]
    module_specifier: str


@dataclass(frozen=True)
class ModuleResolutionConfig:
    base_url: Optional[str] = None
    paths: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class ParsedSource:
    parser: str
    symbols: List[Symbol]
    dependencies: Set[str]
    module_specifiers: Set[str] = field(default_factory=set)
    import_bindings: List[ImportBinding] = field(default_factory=list)


@dataclass
class ImpactItem:
    """Impact analysis for one changed file."""

    file_path: str
    language: str
    changed_lines: List[int]
    changed_symbols: List[Symbol]
    direct_callers: List[Symbol]
    transitive_callers: List[Symbol]
    dependent_files: List[str]
    dependency_modules: List[str]
    retrieved_context: List[Symbol]
    risk_level: str
    risk_score: int
    reasons: List[str]


@dataclass
class ImpactReport:
    """PR-level impact analysis report."""

    items: List[ImpactItem]
    parser: str
    indexed_files: int
    indexed_symbols: int

    @property
    def risk_level(self) -> str:
        if not self.items:
            return "low"
        levels = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return max((item.risk_level for item in self.items), key=lambda level: levels[level])

    def to_context(self, max_symbols_per_file: int = 8) -> str:
        """Return concise Markdown context for LLM review prompts."""
        if not self.items:
            return "## Repo-Level Impact Analysis\nNo repo impact data available."

        lines = [
            "## Repo-Level Impact Analysis",
            f"- Indexed files: {self.indexed_files}",
            f"- Indexed symbols: {self.indexed_symbols}",
            f"- Parser: {self.parser}",
            f"- Overall risk: {self.risk_level}",
            "",
        ]

        for item in self.items:
            lines.append(f"### {item.file_path}")
            lines.append(f"- Risk: {item.risk_level} ({item.risk_score}/100)")
            if item.changed_symbols:
                changed = ", ".join(_format_symbol(sym) for sym in item.changed_symbols[:max_symbols_per_file])
                lines.append(f"- Changed symbols: {changed}")
            elif item.changed_lines:
                lines.append(f"- Changed lines: {item.changed_lines[:12]}")
            else:
                lines.append("- Changed symbols: file-level change")

            if item.direct_callers:
                callers = ", ".join(_format_symbol(sym) for sym in item.direct_callers[:max_symbols_per_file])
                lines.append(f"- Direct callers: {callers}")
            if item.transitive_callers:
                callers = ", ".join(_format_symbol(sym) for sym in item.transitive_callers[:max_symbols_per_file])
                lines.append(f"- Transitive callers: {callers}")
            if item.dependent_files:
                files = ", ".join(f"`{path}`" for path in item.dependent_files[:max_symbols_per_file])
                lines.append(f"- Files importing this module: {files}")
            if item.reasons:
                lines.append("- Risk reasons: " + "; ".join(item.reasons[:4]))
            lines.append("")

        return "\n".join(lines).strip()

    def to_comment_markdown(self) -> str:
        """Return PR comment section for impact analysis."""
        if not self.items:
            return "### 🧭 Impact Analysis\n\n未能生成影响范围分析。"

        rows = [
            "### 🧭 Impact Analysis",
            "",
            f"整体风险: **{self.risk_level}** · 索引文件: {self.indexed_files} · 符号: {self.indexed_symbols}",
            "",
            "| 文件 | 风险 | 变更符号 | 调用方 / 依赖 |",
            "|------|------|----------|---------------|",
        ]

        for item in self.items:
            changed = ", ".join(f"`{sym.qualified_name}`" for sym in item.changed_symbols[:4])
            if not changed:
                changed = "文件级变更"

            related_count = len(item.direct_callers) + len(item.transitive_callers) + len(item.dependent_files)
            related = f"{related_count} 个相关符号/文件"
            if item.direct_callers:
                related += f"; 直接调用方 {len(item.direct_callers)}"
            if item.dependent_files:
                related += f"; 依赖文件 {len(item.dependent_files)}"

            rows.append(
                f"| `{item.file_path}` | {item.risk_level} ({item.risk_score}) | "
                f"{changed} | {related} |"
            )

        detail_lines = ["", "<details>", "<summary>影响范围详情</summary>", ""]
        for item in self.items:
            detail_lines.append(f"**`{item.file_path}`**")
            if item.direct_callers:
                detail_lines.append(
                    "- Direct callers: "
                    + ", ".join(_format_symbol(sym) for sym in item.direct_callers[:10])
                )
            if item.transitive_callers:
                detail_lines.append(
                    "- Transitive callers: "
                    + ", ".join(_format_symbol(sym) for sym in item.transitive_callers[:10])
                )
            if item.dependent_files:
                detail_lines.append(
                    "- Import dependents: "
                    + ", ".join(f"`{path}`" for path in item.dependent_files[:10])
                )
            if item.reasons:
                detail_lines.append("- Reasons: " + "; ".join(item.reasons[:5]))
            detail_lines.append("")
        detail_lines.extend(["</details>"])

        return "\n".join(rows + detail_lines)


class RepoIndex:
    """Symbol index that supports repo-level context retrieval."""

    def __init__(self):
        self.files: Dict[str, SourceFile] = {}
        self.symbols: Dict[str, Symbol] = {}
        self.symbols_by_file: Dict[str, List[Symbol]] = defaultdict(list)
        self.symbols_by_name: Dict[str, List[Symbol]] = defaultdict(list)
        self.callers_by_symbol_id: Dict[str, Set[str]] = defaultdict(set)
        self.dependencies_by_file: Dict[str, Set[str]] = defaultdict(set)
        self.dependents_by_module: Dict[str, Set[str]] = defaultdict(set)
        self.import_bindings_by_file: Dict[str, List[ImportBinding]] = defaultdict(list)
        self._source_paths: Set[str] = set()
        self._module_configs: List[Tuple[str, ModuleResolutionConfig]] = []
        self.parser = "heuristic"

    @classmethod
    def build(cls, files: Sequence[SourceFile]) -> "RepoIndex":
        index = cls()
        parser_kinds = set()
        source_paths = {
            source.path for source in files
            if source.language in {"python", "javascript", "typescript"}
        }
        module_configs = _load_module_resolution_configs(files)
        index._source_paths = source_paths
        index._module_configs = module_configs

        for source in files:
            if not source.language:
                continue
            if source.language == "json":
                continue
            index.files[source.path] = source
            parsed = _parse_source_file(source)
            parser_kinds.add(parsed.parser)
            dependencies = set(parsed.dependencies)

            if source.language in _JS_TS_LANGUAGES:
                config = _select_module_resolution_config(source.path, module_configs)
                for specifier in parsed.module_specifiers:
                    dependencies.update(
                        _resolve_js_ts_module_keys(
                            specifier=specifier,
                            importer_path=source.path,
                            source_paths=source_paths,
                            config=config,
                        )
                    )
                index.import_bindings_by_file[source.path] = parsed.import_bindings

            index.dependencies_by_file[source.path] = dependencies

            for symbol in parsed.symbols:
                index.symbols[symbol.id] = symbol
                index.symbols_by_file[symbol.file_path].append(symbol)
                index.symbols_by_name[symbol.name].append(symbol)
                index.symbols_by_name[symbol.qualified_name].append(symbol)

        for path, deps in index.dependencies_by_file.items():
            for dep in deps:
                index.dependents_by_module[dep].add(path)

        index._resolve_callers()
        if parser_kinds:
            index.parser = "+".join(sorted(parser_kinds))
        return index

    def _resolve_callers(self) -> None:
        for caller in self.symbols.values():
            for callee_name in caller.calls:
                matched_callees: Set[str] = set()
                for callee in self.symbols_by_name.get(callee_name, []):
                    if callee.id != caller.id:
                        self.callers_by_symbol_id[callee.id].add(caller.id)
                        matched_callees.add(callee.id)

                for callee in self._resolve_imported_callees(caller, callee_name):
                    if callee.id != caller.id and callee.id not in matched_callees:
                        self.callers_by_symbol_id[callee.id].add(caller.id)

    def _resolve_imported_callees(self, caller: Symbol, callee_name: str) -> List[Symbol]:
        bindings = [
            binding for binding in self.import_bindings_by_file.get(caller.file_path, [])
            if binding.local_name == callee_name
        ]
        if not bindings:
            return []

        resolved: List[Symbol] = []
        for binding in bindings:
            target_files = self._files_for_dependency_specifier(caller.file_path, binding.module_specifier)
            for target_file in target_files:
                symbols = self.symbols_by_file.get(target_file, [])
                if binding.imported_name in (None, "*"):
                    resolved.extend(symbols)
                elif binding.imported_name == "default":
                    resolved.extend(_default_export_candidates(symbols))
                else:
                    resolved.extend(
                        symbol for symbol in symbols
                        if symbol.name == binding.imported_name
                        or symbol.qualified_name.endswith(f".{binding.imported_name}")
                    )
        return resolved

    def _files_for_dependency_specifier(self, importer_path: str, specifier: str) -> List[str]:
        module_keys = _module_keys_for_specifier_text(specifier)
        candidates = set()
        for key in module_keys:
            candidates.update(self.dependents_by_module.get(key, set()))

        # The reverse map answers "who imports this module"; here we need the
        # target file, so resolve against the indexed file list as well.
        source_paths = self._source_paths or set(self.files)
        config = _select_module_resolution_config(importer_path, self._module_configs)
        resolved_keys = _resolve_js_ts_module_keys(
            specifier=specifier,
            importer_path=importer_path,
            source_paths=source_paths,
            config=config,
        )
        target_files = [
            path for path in source_paths
            if path in resolved_keys or _path_without_suffix(path) in resolved_keys
        ]
        if target_files:
            return sorted(target_files)
        return sorted(candidates)

    def get_changed_symbols(self, file_path: str, changed_lines: Set[int]) -> List[Symbol]:
        symbols = self.symbols_by_file.get(file_path, [])
        changed = [symbol for symbol in symbols if symbol.overlaps_lines(changed_lines)]
        if changed:
            return sorted(changed, key=lambda sym: (sym.start_line, sym.qualified_name))

        if changed_lines:
            return []
        return sorted(symbols, key=lambda sym: (sym.start_line, sym.qualified_name))

    def get_callers(self, symbols: Sequence[Symbol], depth: int = 1) -> List[Symbol]:
        seen: Set[str] = set()
        result: List[Symbol] = []
        queue = deque((symbol.id, 0) for symbol in symbols)

        while queue:
            symbol_id, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for caller_id in sorted(self.callers_by_symbol_id.get(symbol_id, set())):
                if caller_id in seen:
                    continue
                seen.add(caller_id)
                caller = self.symbols[caller_id]
                result.append(caller)
                queue.append((caller_id, current_depth + 1))

        return result

    def get_dependents_for_file(self, file_path: str) -> List[str]:
        candidates = set()
        for key in _module_keys_for_path(file_path):
            candidates.update(self.dependents_by_module.get(key, set()))

        return sorted(path for path in candidates if path != file_path)

    def retrieve_context(self, query_symbols: Sequence[Symbol], file_path: str, limit: int = 8) -> List[Symbol]:
        """Symbol-index based repo RAG for changed code."""
        ranked: Dict[str, Tuple[int, Symbol]] = {}

        def add(symbol: Symbol, score: int) -> None:
            if symbol.file_path == file_path and score < 80:
                return
            current = ranked.get(symbol.id)
            if current is None or score > current[0]:
                ranked[symbol.id] = (score, symbol)

        for symbol in query_symbols:
            for caller in self.get_callers([symbol], depth=1):
                add(caller, 100)
            for caller in self.get_callers([symbol], depth=2):
                add(caller, 70)
            for callee_name in symbol.calls:
                for callee in self.symbols_by_name.get(callee_name, []):
                    add(callee, 55)

        for dependent in self.get_dependents_for_file(file_path):
            for symbol in self.symbols_by_file.get(dependent, [])[:3]:
                add(symbol, 45)

        return [
            symbol
            for _, symbol in sorted(ranked.values(), key=lambda item: (-item[0], item[1].file_path, item[1].start_line))
        ][:limit]

    def analyze_impact(self, changed_files: Sequence[Tuple[str, str, str]]) -> ImpactReport:
        """Analyze changed files from tuples of (path, language, patch)."""
        items: List[ImpactItem] = []

        for file_path, language, patch in changed_files:
            changed_lines = _extract_added_line_numbers(patch)
            changed_symbols = self.get_changed_symbols(file_path, changed_lines)
            direct_callers = self.get_callers(changed_symbols, depth=1)
            all_callers = self.get_callers(changed_symbols, depth=2)
            direct_ids = {symbol.id for symbol in direct_callers}
            transitive_callers = [symbol for symbol in all_callers if symbol.id not in direct_ids]
            dependent_files = self.get_dependents_for_file(file_path)
            dependency_modules = sorted(self.dependencies_by_file.get(file_path, set()))
            retrieved_context = self.retrieve_context(changed_symbols, file_path)
            risk_score, risk_level, reasons = _score_risk(
                file_path=file_path,
                changed_symbols=changed_symbols,
                direct_callers=direct_callers,
                transitive_callers=transitive_callers,
                dependent_files=dependent_files,
                patch=patch,
            )

            items.append(
                ImpactItem(
                    file_path=file_path,
                    language=language,
                    changed_lines=sorted(changed_lines),
                    changed_symbols=changed_symbols,
                    direct_callers=direct_callers,
                    transitive_callers=transitive_callers,
                    dependent_files=dependent_files,
                    dependency_modules=dependency_modules,
                    retrieved_context=retrieved_context,
                    risk_level=risk_level,
                    risk_score=risk_score,
                    reasons=reasons,
                )
            )

        return ImpactReport(
            items=items,
            parser=self.parser,
            indexed_files=len(self.files),
            indexed_symbols=len(self.symbols),
        )


def source_file_from_github_blob(path: str, encoded_content: str, encoding: str = "base64") -> Optional[SourceFile]:
    """Create a SourceFile from GitHub blob content."""
    language = detect_language(path)
    if not language:
        return None
    if encoding != "base64":
        return None
    try:
        content = base64.b64decode(encoded_content).decode("utf-8", errors="replace")
    except Exception:
        return None
    return SourceFile(path=path, content=content, language=language)


def detect_language(path: str) -> Optional[str]:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in _LANGUAGE_BY_SUFFIX:
        return None
    if any(part in _SKIP_PATH_PARTS for part in PurePosixPath(path).parts):
        return None
    return _LANGUAGE_BY_SUFFIX[suffix]


def _parse_source_file(source: SourceFile) -> ParsedSource:
    if source.language == "python":
        symbols, dependencies = _parse_python_ast(source)
        if symbols or dependencies:
            return ParsedSource("python-ast", symbols, dependencies)

    if source.language in _JS_TS_LANGUAGES:
        parsed = _parse_js_ts_with_treesitter(source)
        if parsed is not None:
            return parsed

    parsed = _parse_with_treesitter(source)
    if parsed is not None:
        symbols, dependencies = parsed
        return ParsedSource("tree-sitter", symbols, dependencies)

    symbols, dependencies = _parse_text_heuristic(source)
    module_specifiers = dependencies if source.language in _JS_TS_LANGUAGES else set()
    return ParsedSource("heuristic", symbols, dependencies, module_specifiers=module_specifiers)


def _parse_python_ast(source: SourceFile) -> Tuple[List[Symbol], Set[str]]:
    try:
        tree = ast.parse(source.content)
    except SyntaxError:
        return [], set()

    dependencies: Set[str] = set()
    symbols: List[Symbol] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dependencies.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            dependencies.add(node.module)

    def visit_body(nodes: Iterable[ast.AST], parents: List[str]) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                qualified_name = ".".join(parents + [node.name])
                symbols.append(
                    Symbol(
                        id=_symbol_id(source.path, qualified_name, node.lineno),
                        name=node.name,
                        qualified_name=qualified_name,
                        kind="class",
                        file_path=source.path,
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        calls=_collect_python_calls(node),
                        dependencies=set(dependencies),
                    )
                )
                visit_body(node.body, parents + [node.name])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified_name = ".".join(parents + [node.name])
                symbols.append(
                    Symbol(
                        id=_symbol_id(source.path, qualified_name, node.lineno),
                        name=node.name,
                        qualified_name=qualified_name,
                        kind="function",
                        file_path=source.path,
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        calls=_collect_python_calls(node),
                        dependencies=set(dependencies),
                    )
                )
                visit_body(node.body, parents + [node.name])

    visit_body(tree.body, [])
    return symbols, dependencies


def _collect_python_calls(node: ast.AST) -> Set[str]:
    calls: Set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                calls.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                calls.add(child.func.attr)
    return calls


def _parse_js_ts_with_treesitter(source: SourceFile) -> Optional[ParsedSource]:
    parser = _make_treesitter_parser(source.language)
    if parser is None:
        return None

    code_bytes = source.content.encode("utf-8")
    try:
        tree = parser.parse(code_bytes)
    except Exception:
        return None

    dependencies, module_specifiers, import_bindings = _collect_js_ts_imports(
        tree.root_node, code_bytes
    )
    symbols: List[Symbol] = []

    def text(node) -> str:
        return code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def node_name(node) -> Optional[str]:
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return text(name_node)
        for child in node.children:
            if child.type in {"identifier", "type_identifier", "property_identifier", "private_property_identifier"}:
                return text(child).lstrip("#")
        return None

    def node_value(node):
        value = node.child_by_field_name("value")
        if value is not None:
            return value
        for child in node.children:
            if child.type in {
                "arrow_function",
                "function",
                "function_declaration",
                "generator_function",
            }:
                return child
        return None

    def add_symbol(node, name: str, kind: str, parents: List[str]) -> None:
        qualified_name = ".".join(parents + [name])
        symbols.append(
            Symbol(
                id=_symbol_id(source.path, qualified_name, node.start_point[0] + 1),
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                file_path=source.path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                calls=_collect_treesitter_calls(node, code_bytes),
                dependencies=set(dependencies),
            )
        )

    def traverse(node, parents: List[str]) -> None:
        if _is_nested_non_symbol_function(node):
            return

        node_type = node.type
        next_parents = parents

        if node_type in {"function_declaration", "generator_function_declaration"}:
            name = node_name(node)
            if name:
                add_symbol(node, name, "function", parents)
            return

        if node_type == "class_declaration":
            name = node_name(node)
            if name:
                add_symbol(node, name, "class", parents)
                next_parents = parents + [name]

        elif node_type == "method_definition":
            name = node_name(node)
            if name:
                add_symbol(node, name, "method", parents)
            return

        elif node_type in {"field_definition", "public_field_definition"}:
            value = node_value(node)
            name = node_name(node)
            if name and value is not None and value.type in {"arrow_function", "function"}:
                add_symbol(node, name, "method" if parents else "function", parents)
            return

        elif node_type == "variable_declarator":
            value = node_value(node)
            name = node_name(node)
            if name and value is not None and value.type in {"arrow_function", "function"}:
                add_symbol(node, name, "function", parents)
                return

        elif node_type == "pair":
            value = node_value(node)
            key = node.child_by_field_name("key")
            if key is not None and value is not None and value.type in {"arrow_function", "function"}:
                add_symbol(node, text(key).strip("'\""), "method", parents)
                return

        for child in node.children:
            traverse(child, next_parents)

    traverse(tree.root_node, [])

    return ParsedSource(
        parser=f"{source.language}-tree-sitter",
        symbols=symbols,
        dependencies=dependencies,
        module_specifiers=module_specifiers,
        import_bindings=import_bindings,
    )


def _is_nested_non_symbol_function(node) -> bool:
    return node.type in {"arrow_function", "function"} and node.parent is not None and node.parent.type not in {
        "variable_declarator",
        "field_definition",
        "public_field_definition",
        "pair",
    }


def _collect_js_ts_imports(root_node, code_bytes: bytes) -> Tuple[Set[str], Set[str], List[ImportBinding]]:
    dependencies: Set[str] = set()
    module_specifiers: Set[str] = set()
    import_bindings: List[ImportBinding] = []

    def text(node) -> str:
        return code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def string_literal(node) -> Optional[str]:
        if node is None or node.type != "string":
            return None
        value = text(node).strip()
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            return value[1:-1]
        return value

    def find_string_child(node) -> Optional[str]:
        if node.type == "string":
            return string_literal(node)
        for child in node.children:
            found = find_string_child(child)
            if found is not None:
                return found
        return None

    def add_module(specifier: Optional[str]) -> None:
        if not specifier:
            return
        module_specifiers.add(specifier)
        dependencies.update(_module_keys_for_specifier_text(specifier))

    def collect_import_clause(statement, specifier: str) -> None:
        for child in statement.children:
            if child.type == "import_clause":
                for clause_child in child.children:
                    if clause_child.type == "identifier":
                        import_bindings.append(ImportBinding(clause_child_text(clause_child), "default", specifier))
                    elif clause_child.type == "namespace_import":
                        local = _last_identifier_text(clause_child)
                        if local:
                            import_bindings.append(ImportBinding(local, "*", specifier))
                    elif clause_child.type == "named_imports":
                        for spec in _descendants_of_type(clause_child, {"import_specifier"}):
                            imported = spec.child_by_field_name("name")
                            alias = spec.child_by_field_name("alias")
                            imported_name = clause_child_text(imported) if imported is not None else _first_identifier_text(spec)
                            local_name = clause_child_text(alias) if alias is not None else imported_name
                            if local_name:
                                import_bindings.append(ImportBinding(local_name, imported_name, specifier))

    def clause_child_text(node) -> str:
        return text(node).lstrip("#")

    def _first_identifier_text(node) -> Optional[str]:
        for child in node.children:
            if child.type in {"identifier", "type_identifier", "property_identifier"}:
                return clause_child_text(child)
        return None

    def _last_identifier_text(node) -> Optional[str]:
        found = None
        for child in node.children:
            if child.type in {"identifier", "type_identifier", "property_identifier"}:
                found = clause_child_text(child)
        return found

    def visit(node) -> None:
        if node.type == "import_statement":
            specifier = find_string_child(node)
            add_module(specifier)
            if specifier:
                collect_import_clause(node, specifier)
            return

        if node.type == "export_statement":
            add_module(find_string_child(node))
            return

        if node.type == "call_expression":
            function_node = node.child_by_field_name("function")
            if function_node is not None and text(function_node) == "require":
                add_module(find_string_child(node))

        for child in node.children:
            visit(child)

    visit(root_node)
    return dependencies, module_specifiers, import_bindings


def _descendants_of_type(node, types: Set[str]):
    for child in node.children:
        if child.type in types:
            yield child
        yield from _descendants_of_type(child, types)


def _parse_with_treesitter(source: SourceFile) -> Optional[Tuple[List[Symbol], Set[str]]]:
    parser = _make_treesitter_parser(source.language)
    if parser is None:
        return None

    code_bytes = source.content.encode("utf-8")
    try:
        tree = parser.parse(code_bytes)
    except Exception:
        return None

    dependencies = _extract_text_dependencies(source.content, source.language)
    symbols: List[Symbol] = []
    lines = source.content.splitlines()

    def text(node) -> str:
        return code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def node_name(node) -> Optional[str]:
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return text(name_node)
        return None

    def traverse(node, parents: List[str]) -> None:
        node_type = node.type
        kind = None
        name = None

        if node_type in {"function_declaration", "method_definition", "function_definition"}:
            kind = "function"
            name = node_name(node)
        elif node_type in {"class_declaration", "class_definition"}:
            kind = "class"
            name = node_name(node)
        elif node_type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is not None and value.type in {"arrow_function", "function"}:
                kind = "function"
                name = node_name(node)

        next_parents = parents
        if kind and name:
            qualified_name = ".".join(parents + [name])
            symbols.append(
                Symbol(
                    id=_symbol_id(source.path, qualified_name, node.start_point[0] + 1),
                    name=name,
                    qualified_name=qualified_name,
                    kind=kind,
                    file_path=source.path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    calls=_collect_treesitter_calls(node, code_bytes),
                    dependencies=set(dependencies),
                )
            )
            if kind == "class":
                next_parents = parents + [name]

        for child in node.children:
            traverse(child, next_parents)

    traverse(tree.root_node, [])

    if not symbols:
        return [], dependencies
    return symbols, dependencies


def _make_treesitter_parser(language: str):
    try:
        from tree_sitter import Language, Parser

        if language == "python":
            import tree_sitter_python as ts_language_module
            language_obj = Language(ts_language_module.language())
        elif language == "javascript":
            import tree_sitter_javascript as ts_language_module
            language_obj = Language(ts_language_module.language())
        elif language == "typescript":
            import tree_sitter_typescript as ts_language_module
            language_obj = Language(ts_language_module.language_typescript())
        else:
            return None

        try:
            return Parser(language_obj)
        except TypeError:
            parser = Parser()
            parser.set_language(language_obj)
            return parser
    except Exception:
        return None


def _collect_treesitter_calls(node, code_bytes: bytes) -> Set[str]:
    calls: Set[str] = set()

    def text(child) -> str:
        return code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

    def traverse(child) -> None:
        if child.type in {"call", "call_expression"}:
            function_node = child.child_by_field_name("function")
            if function_node is not None:
                if function_node.type == "identifier":
                    calls.add(text(function_node))
                else:
                    property_node = function_node.child_by_field_name("property")
                    attribute_node = function_node.child_by_field_name("attribute")
                    if property_node is not None:
                        calls.add(text(property_node))
                    elif attribute_node is not None:
                        calls.add(text(attribute_node))
        for grandchild in child.children:
            traverse(grandchild)

    traverse(node)
    return calls


def _parse_text_heuristic(source: SourceFile) -> Tuple[List[Symbol], Set[str]]:
    dependencies = _extract_text_dependencies(source.content, source.language)
    symbols: List[Symbol] = []
    lines = source.content.splitlines()

    definition_patterns = [
        re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
        re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b"),
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("),
        re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)\b"),
        re.compile(r"^\s*(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\("),
    ]

    matches: List[Tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        for pattern in definition_patterns:
            match = pattern.search(line)
            if match:
                matches.append((index, match.group(1)))
                break

    for idx, (start_line, name) in enumerate(matches):
        end_line = matches[idx + 1][0] - 1 if idx + 1 < len(matches) else len(lines)
        body = "\n".join(lines[start_line - 1:end_line])
        calls = set(re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", body))
        calls.discard(name)
        symbols.append(
            Symbol(
                id=_symbol_id(source.path, name, start_line),
                name=name,
                qualified_name=name,
                kind="function",
                file_path=source.path,
                start_line=start_line,
                end_line=end_line,
                calls=calls,
                dependencies=set(dependencies),
            )
        )

    return symbols, dependencies


def _extract_text_dependencies(content: str, language: str) -> Set[str]:
    dependencies: Set[str] = set()
    if language == "python":
        for match in re.finditer(r"^\s*import\s+([A-Za-z0-9_.,\s]+)", content, re.MULTILINE):
            for name in match.group(1).split(","):
                dependencies.add(name.strip().split(".")[0])
        for match in re.finditer(r"^\s*from\s+([A-Za-z0-9_.]+)\s+import\s+", content, re.MULTILINE):
            dependencies.add(match.group(1))
    else:
        patterns = [
            r"from\s+['\"]([^'\"]+)['\"]",
            r"import\s+['\"]([^'\"]+)['\"]",
            r"require\(['\"]([^'\"]+)['\"]\)",
        ]
        for pattern in patterns:
            dependencies.update(re.findall(pattern, content))
    return {_normalize_dependency(dep) for dep in dependencies if dep}


def _normalize_dependency(dep: str) -> str:
    dep = dep.strip()
    if dep.startswith("."):
        dep = dep.lstrip("./")
    if "/" in dep:
        dep = dep.rsplit("/", 1)[-1]
    return dep.removesuffix(".py").removesuffix(".js").removesuffix(".ts")


def _load_module_resolution_configs(files: Sequence[SourceFile]) -> List[Tuple[str, ModuleResolutionConfig]]:
    configs: List[Tuple[str, ModuleResolutionConfig]] = []
    for source in files:
        path = PurePosixPath(source.path)
        if path.name not in _CONFIG_FILENAMES:
            continue
        try:
            data = json.loads(_strip_json_comments(source.content))
        except json.JSONDecodeError:
            continue
        compiler_options = data.get("compilerOptions", {}) if isinstance(data, dict) else {}
        if not isinstance(compiler_options, dict):
            continue

        base_url = compiler_options.get("baseUrl")
        paths = compiler_options.get("paths", {})
        if not isinstance(paths, dict):
            paths = {}

        normalized_paths: Dict[str, List[str]] = {}
        for alias, targets in paths.items():
            if isinstance(targets, str):
                targets = [targets]
            if isinstance(targets, list):
                normalized_paths[alias] = [target for target in targets if isinstance(target, str)]

        config_dir = "" if str(path.parent) == "." else str(path.parent)
        base_prefix = _join_posix(config_dir, base_url) if isinstance(base_url, str) else config_dir
        configs.append((
            config_dir,
            ModuleResolutionConfig(base_url=base_prefix or None, paths=normalized_paths),
        ))

    return sorted(configs, key=lambda item: len(PurePosixPath(item[0]).parts), reverse=True)


def _strip_json_comments(content: str) -> str:
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", content, flags=re.MULTILINE)


def _select_module_resolution_config(
    file_path: str,
    configs: Sequence[Tuple[str, ModuleResolutionConfig]],
) -> ModuleResolutionConfig:
    for config_dir, config in configs:
        if not config_dir or file_path == config_dir or file_path.startswith(config_dir.rstrip("/") + "/"):
            return config
    return ModuleResolutionConfig()


def _resolve_js_ts_module_keys(
    specifier: str,
    importer_path: str,
    source_paths: Set[str],
    config: ModuleResolutionConfig,
) -> Set[str]:
    keys = set(_module_keys_for_specifier_text(specifier))
    resolved_paths: Set[str] = set()

    candidate_roots: List[str] = []
    alias_roots = _alias_candidate_roots(specifier, config)

    if specifier.startswith("."):
        importer_dir = str(PurePosixPath(importer_path).parent)
        if importer_dir == ".":
            importer_dir = ""
        candidate_roots.append(_join_posix(importer_dir, specifier))
    elif alias_roots:
        candidate_roots.extend(alias_roots)
    elif specifier.startswith("/") or specifier.startswith("@/"):
        stripped = specifier[1:] if specifier.startswith("/") else specifier[2:]
        base = config.base_url or ""
        candidate_roots.append(_join_posix(base, stripped))
    else:
        if config.base_url:
            candidate_roots.append(_join_posix(config.base_url, specifier))

    for root in candidate_roots:
        resolved_paths.update(_resolve_js_ts_candidate_path(root, source_paths))

    for path in resolved_paths:
        keys.update(_module_keys_for_path(path))
        keys.add(path)
        keys.add(_path_without_suffix(path))

    return keys


def _alias_candidate_roots(specifier: str, config: ModuleResolutionConfig) -> List[str]:
    roots: List[str] = []
    for alias, targets in config.paths.items():
        if "*" in alias:
            prefix, suffix = alias.split("*", 1)
            if not specifier.startswith(prefix) or not specifier.endswith(suffix):
                continue
            wildcard = specifier[len(prefix): len(specifier) - len(suffix) if suffix else len(specifier)]
            for target in targets:
                roots.append(_join_posix(config.base_url or "", target.replace("*", wildcard)))
        elif specifier == alias:
            for target in targets:
                roots.append(_join_posix(config.base_url or "", target))
    return roots


def _resolve_js_ts_candidate_path(root: str, source_paths: Set[str]) -> Set[str]:
    root = _normalize_posix_path(root)
    candidates = []
    suffix = PurePosixPath(root).suffix
    if suffix in _JS_TS_EXTENSIONS:
        candidates.append(root)
    else:
        candidates.extend(root + ext for ext in _JS_TS_EXTENSIONS)
        candidates.extend(_join_posix(root, "index" + ext) for ext in _JS_TS_EXTENSIONS)

    return {candidate for candidate in candidates if candidate in source_paths}


def _module_keys_for_specifier_text(specifier: str) -> Set[str]:
    clean = specifier.strip()
    if not clean:
        return set()
    no_suffix = _strip_js_ts_suffix(clean)
    keys = {clean, no_suffix, PurePosixPath(no_suffix).name}
    if clean.startswith("."):
        stripped = clean.lstrip("./")
        keys.add(stripped)
        keys.add(_strip_js_ts_suffix(stripped))
    return {key for key in keys if key and key != "."}


def _module_keys_for_path(file_path: str) -> Set[str]:
    path = _normalize_posix_path(file_path)
    no_suffix = _path_without_suffix(path)
    keys = {path, no_suffix, PurePosixPath(no_suffix).name, _module_name_for_path(file_path)}
    if PurePosixPath(no_suffix).name == "index":
        parent = str(PurePosixPath(no_suffix).parent)
        if parent and parent != ".":
            keys.add(parent)
            keys.add(PurePosixPath(parent).name)
    return {key for key in keys if key}


def _path_without_suffix(file_path: str) -> str:
    path = PurePosixPath(file_path)
    return str(path.with_suffix(""))


def _strip_js_ts_suffix(value: str) -> str:
    for suffix in _JS_TS_EXTENSIONS:
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _join_posix(*parts: Optional[str]) -> str:
    output = PurePosixPath("")
    for part in parts:
        if not part:
            continue
        clean = str(part).strip()
        if not clean:
            continue
        output = output / clean
    return _normalize_posix_path(str(output))


def _normalize_posix_path(path: str) -> str:
    if not path:
        return ""
    normalized = PurePosixPath(path).as_posix()
    parts = []
    for part in normalized.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _default_export_candidates(symbols: Sequence[Symbol]) -> List[Symbol]:
    if not symbols:
        return []
    top_level = [symbol for symbol in symbols if "." not in symbol.qualified_name]
    return top_level or list(symbols)


def _extract_added_line_numbers(patch: str) -> Set[int]:
    lines: Set[int] = set()
    current_new_line: Optional[int] = None

    for line in (patch or "").splitlines():
        header = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if header:
            current_new_line = int(header.group(1))
            continue
        if current_new_line is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            lines.add(current_new_line)
            current_new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            current_new_line += 1

    return lines


def _score_risk(
    file_path: str,
    changed_symbols: Sequence[Symbol],
    direct_callers: Sequence[Symbol],
    transitive_callers: Sequence[Symbol],
    dependent_files: Sequence[str],
    patch: str,
) -> Tuple[int, str, List[str]]:
    score = 10
    reasons: List[str] = []

    if changed_symbols:
        score += min(25, len(changed_symbols) * 5)
        reasons.append(f"{len(changed_symbols)} changed symbol(s)")
    else:
        score += 15
        reasons.append("file-level or unscoped change")

    if direct_callers:
        score += min(30, len(direct_callers) * 7)
        reasons.append(f"{len(direct_callers)} direct caller(s)")
    if transitive_callers:
        score += min(15, len(transitive_callers) * 3)
        reasons.append(f"{len(transitive_callers)} transitive caller(s)")
    if dependent_files:
        score += min(20, len(dependent_files) * 4)
        reasons.append(f"{len(dependent_files)} importing/dependent file(s)")

    searchable_text = " ".join(
        [file_path, patch]
        + [symbol.name for symbol in changed_symbols]
        + [symbol.qualified_name for symbol in changed_symbols]
    ).lower()
    matched_keywords = sorted(keyword for keyword in _RISK_KEYWORDS if keyword in searchable_text)
    if matched_keywords:
        score += 20
        reasons.append("sensitive path: " + ", ".join(matched_keywords[:5]))

    score = min(score, 100)
    if score >= 80:
        level = "critical"
    elif score >= 60:
        level = "high"
    elif score >= 35:
        level = "medium"
    else:
        level = "low"
    return score, level, reasons


def _module_name_for_path(file_path: str) -> str:
    path = PurePosixPath(file_path)
    return ".".join(path.with_suffix("").parts)


def _symbol_id(file_path: str, qualified_name: str, start_line: int) -> str:
    return f"{file_path}:{qualified_name}:{start_line}"


def _format_symbol(symbol: Symbol) -> str:
    return f"`{symbol.qualified_name}` ({symbol.file_path}:{symbol.start_line})"
