"""
Prompt 模板加载器 — 从 TOML 文件加载并渲染 Jinja2 模板

功能：
- 加载 settings/prompts.toml 和 settings/user_messages.toml 中的默认模板
- 支持仓库级覆盖：读取目标仓库根目录的 .code-review.toml
- 使用 Jinja2 渲染模板变量
- 支持 [extra_instructions] section 追加全局指令
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

from jinja2 import Environment, BaseLoader, Undefined

_SETTINGS_DIR = Path(__file__).parent.parent / "settings"
_prompts_cache: Optional[dict] = None
_user_messages_cache: Optional[dict] = None


class _SilentUndefined(Undefined):
    """未定义变量渲染为空字符串，避免模板中未用到的可选变量报错"""

    def __str__(self):
        return ""

    def __iter__(self):
        return iter([])

    def __bool__(self):
        return False


def _load_toml(path: Path) -> dict:
    """加载 TOML 文件"""
    if tomllib is None:
        raise ImportError(
            "需要 tomllib (Python 3.11+) 或 tomli 包来加载 TOML 文件。\n"
            "请运行: pip install tomli"
        )
    with open(path, "rb") as f:
        return tomllib.load(f)


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 中的值覆盖 base"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_default_prompts() -> dict:
    """加载 settings/prompts.toml 默认模板"""
    global _prompts_cache
    if _prompts_cache is None:
        path = _SETTINGS_DIR / "prompts.toml"
        _prompts_cache = _load_toml(path) if path.exists() else {}
    return _prompts_cache


def load_default_user_messages() -> dict:
    """加载 settings/user_messages.toml 默认模板"""
    global _user_messages_cache
    if _user_messages_cache is None:
        path = _SETTINGS_DIR / "user_messages.toml"
        _user_messages_cache = _load_toml(path) if path.exists() else {}
    return _user_messages_cache


def load_repo_overrides(repo_root: Optional[str] = None) -> dict:
    """加载仓库级覆盖配置 .code-review.toml"""
    if not repo_root:
        return {}
    override_path = Path(repo_root) / ".code-review.toml"
    if not override_path.exists():
        return {}
    try:
        return _load_toml(override_path)
    except Exception:
        return {}


def get_prompts(repo_root: Optional[str] = None) -> dict:
    """加载并合并 prompt 配置（默认 + 仓库级覆盖）"""
    prompts = load_default_prompts()
    overrides = load_repo_overrides(repo_root)
    if overrides:
        prompts = _deep_merge(prompts, overrides)
    return prompts


def render_prompt(template_str: str, variables: Dict[str, Any]) -> str:
    """用 Jinja2 渲染模板，未定义变量渲染为空字符串"""
    if not template_str:
        return ""
    env = Environment(loader=BaseLoader(), undefined=_SilentUndefined)
    template = env.from_string(template_str)
    return template.render(variables).strip()


def get_agent_system_prompt(
    agent_type: str,
    variables: Dict[str, Any] = None,
    repo_root: Optional[str] = None,
) -> str:
    """
    获取指定 agent 渲染后的 system prompt

    Args:
        agent_type: agent 名称（correctness/security/maintainability/policy 等）
        variables: 模板变量（scope, language 等）
        repo_root: 仓库根目录（用于加载覆盖配置）

    Returns:
        渲染后的 system prompt 字符串
    """
    variables = variables or {}
    prompts = get_prompts(repo_root)
    agent_config = prompts.get(agent_type, {})

    template_str = agent_config.get("system", "")
    if not template_str:
        return ""

    rendered = render_prompt(template_str, variables)

    # 追加 extra_instructions（如果配置了）
    extra = prompts.get("extra_instructions", {}).get("content", "")
    if extra:
        rendered = rendered + "\n\n" + extra.strip()

    return rendered


def get_agent_scope(
    agent_type: str,
    repo_root: Optional[str] = None,
) -> str:
    """获取 agent 的 diff scope 文本（未渲染）"""
    prompts = get_prompts(repo_root)
    agent_config = prompts.get(agent_type, {})
    return agent_config.get("scope", "")


def get_user_message_template(
    mode: str,
    variables: Dict[str, Any] = None,
    repo_root: Optional[str] = None,
) -> str:
    """
    获取渲染后的 user message

    Args:
        mode: 消息模式（diff_review / code_review / policy_review）
        variables: 模板变量
        repo_root: 仓库根目录

    Returns:
        渲染后的 user message
    """
    variables = variables or {}
    messages = load_default_user_messages()

    # 仓库级覆盖（user_messages section）
    overrides = load_repo_overrides(repo_root)
    if overrides:
        messages = _deep_merge(messages, overrides)

    section = messages.get(mode, {})
    template_str = section.get("template", "")
    if not template_str:
        return ""
    return render_prompt(template_str, variables)


def invalidate_cache():
    """清除缓存（用于测试）"""
    global _prompts_cache, _user_messages_cache
    _prompts_cache = None
    _user_messages_cache = None
