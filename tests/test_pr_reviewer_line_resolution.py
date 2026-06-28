#!/usr/bin/env python3
"""PRReviewer tests for existing_code based inline positioning."""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from integrations.diff_pipeline import DiffPipelineConfig, build_token_aware_diff
from integrations.git_provider import PRDiff
from integrations.pr_reviewer import PRReviewer


def test_collect_inline_issues_resolves_missing_line_from_existing_code():
    diff = PRDiff(
        file_path="src/app.py",
        language="python",
        patch="""@@ -1,3 +1,4 @@
 def index(request):
+    redirect_url = request.GET["next"]
+    return redirect(redirect_url)
     return ok()
""",
        added_lines='    redirect_url = request.GET["next"]\n    return redirect(redirect_url)',
        full_content=(
            "def index(request):\n"
            '    redirect_url = request.GET["next"]\n'
            "    return redirect(redirect_url)\n"
            "    return ok()\n"
        ),
        status="modified",
    )
    pipeline = build_token_aware_diff([diff], DiffPipelineConfig(context_lines=1))
    chunk = pipeline.chunks[0]
    result = {
        "_diff_files": {pipeline.files[0].file_path: pipeline.files[0]},
        "security": {
            "vulnerabilities": [
                {
                    "type": "open_redirect",
                    "severity": "high",
                    "line": None,
                    "existing_code": 'redirect_url = request.GET["next"]\nreturn redirect(redirect_url)',
                    "message": "User-controlled redirect target.",
                    "remediation": "Validate the redirect target.",
                }
            ]
        },
    }
    reviewer = PRReviewer.__new__(PRReviewer)

    inline = reviewer._collect_inline_issues([chunk], [result])

    assert len(inline) == 1
    assert inline[0]["file_path"] == "src/app.py"
    assert inline[0]["line"] == 3
    assert inline[0]["start_line"] == 2
    assert inline[0]["end_line"] == 3
    assert inline[0]["line_resolution"] == "diff_hunk"


def test_collect_inline_issues_keeps_legacy_line_without_existing_code_match():
    diff = PRDiff(
        file_path="src/app.py",
        language="python",
        patch="""@@ -1 +1 @@
-old = 1
+new = 1
""",
        added_lines="new = 1",
        full_content="new = 1\n",
        status="modified",
    )
    pipeline = build_token_aware_diff([diff], DiffPipelineConfig(context_lines=0))
    result = {
        "_diff_files": {pipeline.files[0].file_path: pipeline.files[0]},
        "bug": {
            "bugs": [
                {
                    "type": "logic",
                    "severity": "critical",
                    "line": 1,
                    "existing_code": "missing()",
                    "message": "Legacy line should still work.",
                    "fix": "Fix it.",
                }
            ]
        },
    }
    reviewer = PRReviewer.__new__(PRReviewer)

    inline = reviewer._collect_inline_issues([pipeline.chunks[0]], [result])

    assert len(inline) == 1
    assert inline[0]["line"] == 1
    assert "line_resolution" not in inline[0]
