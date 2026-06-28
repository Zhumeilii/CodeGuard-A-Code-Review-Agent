#!/usr/bin/env python3
"""Prompt loader 单元测试"""
import sys
import os
import tempfile
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agents.prompt_loader import (
    get_agent_scope,
    get_agent_system_prompt,
    get_prompts,
    get_user_message_template,
    invalidate_cache,
    load_default_prompts,
    load_default_user_messages,
    load_repo_overrides,
    render_prompt,
)


def setup_function():
    """每个测试前清除缓存"""
    invalidate_cache()


def test_load_default_prompts():
    """确认默认 prompts.toml 可正确加载"""
    prompts = load_default_prompts()
    assert "correctness" in prompts
    assert "security" in prompts
    assert "maintainability" in prompts
    assert "policy_reviewer" in prompts
    assert "fix_agent" in prompts
    assert "fix_loop" in prompts
    # 每个 agent 应有 system 字段
    assert "system" in prompts["correctness"]
    assert "system" in prompts["maintainability"]
    assert "system" in prompts["security"]


def test_load_default_user_messages():
    """确认默认 user_messages.toml 可正确加载"""
    messages = load_default_user_messages()
    assert "diff_review" in messages
    assert "code_review" in messages
    assert "policy_review" in messages
    assert "template" in messages["diff_review"]
    assert "template" in messages["code_review"]


def test_render_with_variables():
    """确认 Jinja2 变量替换正确"""
    template = "Hello {{ name }}, you are a {{ role }}."
    result = render_prompt(template, {"name": "Alice", "role": "developer"})
    assert result == "Hello Alice, you are a developer."


def test_render_missing_variable_silent():
    """缺少变量时渲染为空字符串（SilentUndefined）"""
    template = "Hello {{ name }}{{ missing_var }}!"
    result = render_prompt(template, {"name": "Bob"})
    assert result == "Hello Bob!"


def test_get_agent_system_prompt_correctness():
    """correctness agent 获取渲染后的 system prompt"""
    prompt = get_agent_system_prompt("correctness", {"scope": "", "language": "python"})
    assert "代码正确性" in prompt
    assert "逻辑错误" in prompt
    assert "JSON" in prompt


def test_get_agent_system_prompt_maintainability_with_language():
    """maintainability agent 的 language 变量被正确替换"""
    prompt = get_agent_system_prompt("maintainability", {"scope": "", "language": "Go"})
    assert "Go" in prompt
    assert "可维护性" in prompt


def test_get_agent_scope():
    """获取 diff scope 文本"""
    scope = get_agent_scope("correctness")
    assert "PR diff" in scope
    assert "正确性" in scope or "变更" in scope


def test_get_user_message_template_diff():
    """diff_review 模式的 user message 渲染"""
    result = get_user_message_template("diff_review", {
        "file_info": "文件: src/main.py\n",
        "language": "python",
        "pr_info": "PR 标题: Fix bug",
        "code": "+print('hello')",
        "repo_info": "",
        "policy_info": "",
    })
    assert "PR diff" in result
    assert "src/main.py" in result
    assert "+print('hello')" in result


def test_get_user_message_template_code():
    """code_review 模式的 user message 渲染"""
    result = get_user_message_template("code_review", {
        "file_info": "",
        "language": "javascript",
        "code": "const x = 1;",
        "repo_info": "",
        "policy_info": "",
    })
    assert "javascript" in result
    assert "const x = 1;" in result


def test_repo_override_merges():
    """仓库级覆盖正确合并"""
    with tempfile.TemporaryDirectory() as tmpdir:
        override_path = Path(tmpdir) / ".code-review.toml"
        override_path.write_text(
            '[maintainability]\nsystem = "Custom maintainability prompt {{ scope }}"\n'
            '[extra_instructions]\ncontent = "Focus on security"\n'
        )
        invalidate_cache()
        prompt = get_agent_system_prompt("maintainability", {"scope": "test"}, repo_root=tmpdir)
        assert "Custom maintainability prompt test" in prompt
        assert "Focus on security" in prompt


def test_repo_override_does_not_affect_other_agents():
    """仓库级覆盖只影响指定 agent"""
    with tempfile.TemporaryDirectory() as tmpdir:
        override_path = Path(tmpdir) / ".code-review.toml"
        override_path.write_text('[maintainability]\nsystem = "OVERRIDDEN"\n')
        invalidate_cache()
        maintainability = get_agent_system_prompt("maintainability", {}, repo_root=tmpdir)
        correctness = get_agent_system_prompt("correctness", {"scope": "", "language": "python"}, repo_root=tmpdir)
        assert "OVERRIDDEN" in maintainability
        assert "正确性" in correctness  # correctness agent 未受影响


def test_extra_instructions_appended():
    """extra_instructions 正确追加到 system prompt 末尾"""
    with tempfile.TemporaryDirectory() as tmpdir:
        override_path = Path(tmpdir) / ".code-review.toml"
        override_path.write_text(
            '[extra_instructions]\ncontent = "Always check for SQL injection"\n'
        )
        invalidate_cache()
        prompt = get_agent_system_prompt("security", {"scope": "", "language": "python"}, repo_root=tmpdir)
        assert "Always check for SQL injection" in prompt
        assert "安全" in prompt  # 原始内容还在


def test_missing_repo_root_uses_defaults():
    """repo_root=None 时使用默认配置"""
    prompt = get_agent_system_prompt("correctness", {"scope": "", "language": "python"})
    assert "正确性" in prompt


def test_nonexistent_agent_returns_empty():
    """不存在的 agent 类型返回空字符串"""
    prompt = get_agent_system_prompt("nonexistent_agent", {})
    assert prompt == ""
