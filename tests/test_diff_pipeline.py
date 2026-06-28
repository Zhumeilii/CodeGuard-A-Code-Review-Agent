#!/usr/bin/env python3
"""Token-aware diff pipeline tests."""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from integrations.diff_pipeline import DiffPipelineConfig, build_token_aware_diff
from integrations.git_provider import PRDiff


def test_pipeline_annotates_hunks_with_line_numbers_and_context():
    diff = PRDiff(
        file_path="src/app.py",
        language="python",
        patch="""@@ -1,3 +1,4 @@
 import os
 def run():
-    return 1
+    return 2
""",
        added_lines="    return 2",
        full_content="import os\ndef run():\n    return 2\n",
        status="modified",
    )

    result = build_token_aware_diff([diff], DiffPipelineConfig(context_lines=1))

    assert len(result.files) == 1
    assert len(result.chunks) == 1
    content = result.chunks[0].content
    assert "Annotated unified diff" in content
    assert "old_line new_line diff_line" in content
    assert "+    return 2" in content
    assert "Extended new-file context" in content
    assert result.total_tokens > 0


def test_pipeline_skips_ignored_files_and_deletion_only_patches():
    ignored = PRDiff(
        file_path="package-lock.json",
        language="json",
        patch="@@ -1 +1 @@\n-old\n+new",
        added_lines="new",
        full_content="new",
        status="modified",
    )
    deletion_only = PRDiff(
        file_path="src/app.py",
        language="python",
        patch="@@ -1 +0,0 @@\n-def old(): pass",
        added_lines="",
        full_content="",
        status="modified",
    )

    result = build_token_aware_diff([ignored, deletion_only])

    assert not result.chunks
    assert len(result.skipped_files) == 2
    reasons = {file.skipped_reason for file in result.skipped_files}
    assert any(reason and "ignored by pattern" in reason for reason in reasons)
    assert "no added or modified lines" in reasons


def test_pipeline_splits_large_diffs_by_token_budget():
    patch_lines = ["@@ -1,80 +1,80 @@"]
    full_content_lines = []
    for index in range(1, 81):
        patch_lines.append(f"-old_value_{index} = {index}")
        patch_lines.append(f"+new_value_{index} = {index}")
        full_content_lines.append(f"new_value_{index} = {index}")

    diff = PRDiff(
        file_path="src/large.py",
        language="python",
        patch="\n".join(patch_lines),
        added_lines="\n".join(full_content_lines),
        full_content="\n".join(full_content_lines),
        status="modified",
    )

    result = build_token_aware_diff(
        [diff],
        DiffPipelineConfig(max_tokens_per_chunk=120, context_lines=0, max_chunks=20),
    )

    assert len(result.chunks) > 1
    assert all(chunk.token_count <= 256 for chunk in result.chunks)
    assert any(chunk.compressed for chunk in result.chunks)


# ── 动态 Chunk 预算测试 ────────────────────────────────────

from integrations.diff_pipeline import _lookup_context_window


def test_from_model_claude_opus(monkeypatch):
    """Claude Opus 200K 窗口应计算出更多 chunks"""
    monkeypatch.delenv("DIFF_MAX_CHUNKS", raising=False)
    monkeypatch.delenv("DIFF_MAX_TOKENS_PER_CHUNK", raising=False)
    config = DiffPipelineConfig.from_model("claude-opus-4-6")
    # (200000 - 1500 - 4000 - 6000) / 6000 = 31.4 → 31
    assert config.max_chunks == 31
    assert config.max_tokens_per_chunk == 6000


def test_from_model_qwen(monkeypatch):
    """Qwen 128K 窗口应计算出较少 chunks"""
    monkeypatch.delenv("DIFF_MAX_CHUNKS", raising=False)
    monkeypatch.delenv("DIFF_MAX_TOKENS_PER_CHUNK", raising=False)
    config = DiffPipelineConfig.from_model("qwen-plus")
    # (128000 - 1500 - 4000 - 6000) / 6000 = 19.4 → 19
    assert config.max_chunks == 19


def test_from_model_env_override(monkeypatch):
    """DIFF_MAX_CHUNKS 环境变量优先于动态计算"""
    monkeypatch.setenv("DIFF_MAX_CHUNKS", "10")
    config = DiffPipelineConfig.from_model("claude-opus-4-6")
    assert config.max_chunks == 10


def test_from_model_unknown_fallback(monkeypatch):
    """未知模型使用默认 128K 上下文窗口"""
    monkeypatch.delenv("DIFF_MAX_CHUNKS", raising=False)
    monkeypatch.delenv("MODEL_CONTEXT_WINDOW", raising=False)
    config = DiffPipelineConfig.from_model("some-unknown-model-v2")
    # (128000 - 11500) / 6000 = 19
    assert config.max_chunks == 19


def test_model_context_window_env_override(monkeypatch):
    """MODEL_CONTEXT_WINDOW 环境变量对未知模型生效"""
    monkeypatch.delenv("DIFF_MAX_CHUNKS", raising=False)
    monkeypatch.setenv("MODEL_CONTEXT_WINDOW", "64000")
    config = DiffPipelineConfig.from_model("some-unknown-model")
    # (64000 - 11500) / 6000 = 8.75 → 8
    assert config.max_chunks == 8


def test_lookup_context_window_keyword_match():
    """关键词匹配：含 claude 的模型名返回 200K"""
    assert _lookup_context_window("my-custom-claude-model") == 200000
    assert _lookup_context_window("gpt-4-0125-preview") == 128000


def test_from_model_minimum_4_chunks(monkeypatch):
    """极小上下文窗口时至少保证 4 个 chunks"""
    monkeypatch.delenv("DIFF_MAX_CHUNKS", raising=False)
    monkeypatch.setenv("MODEL_CONTEXT_WINDOW", "15000")
    config = DiffPipelineConfig.from_model("tiny-model")
    # (15000 - 11500) / 6000 = 0.58 → max(0, 4) = 4
    assert config.max_chunks == 4
