#!/usr/bin/env python3
"""Line resolver tests for the existing_code positioning protocol."""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from integrations.diff_pipeline import DiffPipelineConfig, build_token_aware_diff
from integrations.git_provider import PRDiff
from integrations.line_resolver import resolve_existing_code, resolve_issue_location


def _diff_file():
    diff = PRDiff(
        file_path="src/app.py",
        language="python",
        patch="""@@ -9,6 +9,7 @@
 def handle(user):
     name = user.name
-    return render(name)
+    html = request.args["html"]
+    return render(html)
 
 def done():
""",
        added_lines='    html = request.args["html"]\n    return render(html)',
        full_content=(
            "def before():\n"
            "    pass\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "def handle(user):\n"
            "    name = user.name\n"
            '    html = request.args["html"]\n'
            "    return render(html)\n"
            "\n"
            "def done():\n"
            "\n"
            "def later():\n"
            "    return True\n"
        ),
        status="modified",
    )
    result = build_token_aware_diff([diff], DiffPipelineConfig(context_lines=1))
    return result.files[0]


def test_resolve_existing_code_prefers_new_side_diff_hunk():
    diff_file = _diff_file()

    resolved = resolve_existing_code(
        'html = request.args["html"]\nreturn render(html)',
        diff_file,
    )

    assert resolved is not None
    assert resolved.start_line == 11
    assert resolved.end_line == 12
    assert resolved.line == 12
    assert resolved.source == "diff_hunk"


def test_resolve_existing_code_falls_back_to_full_content():
    diff_file = _diff_file()

    resolved = resolve_existing_code("def later():", diff_file)

    assert resolved is not None
    assert resolved.start_line == 16
    assert resolved.end_line == 16
    assert resolved.source == "full_content"


def test_resolve_existing_code_does_not_match_deleted_only_line():
    diff_file = _diff_file()

    resolved = resolve_existing_code("return render(name)", diff_file)

    assert resolved is None


def test_resolve_issue_location_preserves_legacy_line_when_unresolved():
    issue = {"line": 99, "existing_code": "does_not_exist()", "message": "legacy"}

    resolved = resolve_issue_location(issue, _diff_file())

    assert resolved["line"] == 99
    assert "start_line" not in resolved


def test_resolve_issue_location_overrides_line_when_existing_code_matches():
    issue = {
        "line": 99,
        "existing_code": 'html = request.args["html"]',
        "message": "unsafe input",
    }

    resolved = resolve_issue_location(issue, _diff_file())

    assert resolved["line"] == 11
    assert resolved["start_line"] == 11
    assert resolved["end_line"] == 11
    assert resolved["line_resolution"] == "diff_hunk"
