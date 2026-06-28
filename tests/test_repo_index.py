#!/usr/bin/env python3
"""Repo-level symbol index tests."""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tools.repo_index import RepoIndex, SourceFile


def test_repo_index_builds_callers_and_impact_report():
    files = [
        SourceFile(
            path="src/service.py",
            language="python",
            content="""
def validate_token(token):
    return token == "ok"


def login(token):
    if validate_token(token):
        return "allowed"
    return "denied"
""".strip(),
        ),
        SourceFile(
            path="src/api.py",
            language="python",
            content="""
from src.service import login


def handle_request(token):
    return login(token)
""".strip(),
        ),
        SourceFile(
            path="tests/test_service.py",
            language="python",
            content="""
from src.service import validate_token


def test_validate_token():
    assert validate_token("ok")
""".strip(),
        ),
    ]

    patch = """@@ -1,5 +1,6 @@
 def validate_token(token):
+    if not token:
+        return False
     return token == "ok"
"""

    index = RepoIndex.build(files)
    report = index.analyze_impact([("src/service.py", "python", patch)])
    item = report.items[0]

    changed_names = {symbol.name for symbol in item.changed_symbols}
    caller_names = {symbol.name for symbol in item.direct_callers}

    assert "validate_token" in changed_names
    assert "login" in caller_names
    assert "tests/test_service.py" in item.dependent_files
    assert item.risk_level in {"medium", "high", "critical"}
    assert report.indexed_files == 3
    assert report.indexed_symbols >= 4
    assert "Repo-Level Impact Analysis" in report.to_context()
    assert "Impact Analysis" in report.to_comment_markdown()


def test_repo_index_resolves_typescript_imports_aliases_and_callers():
    files = [
        SourceFile(
            path="tsconfig.json",
            language="json",
            content='{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}',
        ),
        SourceFile(
            path="src/utils/math.ts",
            language="typescript",
            content="""
export const add = (a: number, b: number) => {
  return a + b
}

export function multiply(a: number, b: number) {
  return add(a, b)
}
""".strip(),
        ),
        SourceFile(
            path="src/services/calculator.ts",
            language="typescript",
            content="""
import { add as sum, multiply } from "@/utils/math"

export class Calculator {
  compute(value: number) {
    return sum(value, multiply(value, 2))
  }

  static create = () => new Calculator()
}
""".strip(),
        ),
        SourceFile(
            path="src/index.ts",
            language="typescript",
            content="""
export { add } from "./utils/math"
export * from "./services/calculator"
""".strip(),
        ),
    ]

    patch = """@@ -1,4 +1,5 @@
 export const add = (a: number, b: number) => {
+  if (!Number.isFinite(a)) return b
   return a + b
 }
"""

    index = RepoIndex.build(files)
    report = index.analyze_impact([("src/utils/math.ts", "typescript", patch)])
    item = report.items[0]

    changed_names = {symbol.name for symbol in item.changed_symbols}
    caller_names = {symbol.qualified_name for symbol in item.direct_callers}

    assert "add" in changed_names
    assert "multiply" in caller_names
    assert "Calculator.compute" in caller_names
    assert "src/services/calculator.ts" in item.dependent_files
    assert "src/index.ts" in item.dependent_files
    assert "typescript-tree-sitter" in report.parser


def test_repo_index_resolves_javascript_index_modules_and_methods():
    files = [
        SourceFile(
            path="src/lib/index.js",
            language="javascript",
            content="""
export function normalize(value) {
  return value.trim()
}

export class Formatter {
  format(value) {
    return normalize(value)
  }
}
""".strip(),
        ),
        SourceFile(
            path="src/app.js",
            language="javascript",
            content="""
const { normalize } = require("./lib")

function handle(input) {
  return normalize(input)
}
""".strip(),
        ),
    ]

    patch = """@@ -1,3 +1,4 @@
 export function normalize(value) {
+  if (!value) return ""
   return value.trim()
 }
"""

    index = RepoIndex.build(files)
    report = index.analyze_impact([("src/lib/index.js", "javascript", patch)])
    item = report.items[0]

    changed_names = {symbol.name for symbol in item.changed_symbols}
    caller_names = {symbol.name for symbol in item.direct_callers}

    assert "normalize" in changed_names
    assert "format" in caller_names
    assert "handle" in caller_names
    assert "src/app.js" in item.dependent_files
