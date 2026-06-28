"""
Maintainability Agent - 可维护性审查

负责检查：
- 可读性、命名和复杂函数
- 重复代码和职责边界
- 错误处理策略
- 语言惯用法和模块组织
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class MaintainabilityAgent(BaseAgent):
    """可维护性审查 Agent"""

    def __init__(self, client, model):
        super().__init__(client, model, "maintainability")

    async def analyze(self, code: str, language: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """分析可维护性问题"""
        from agents.prompt_loader import get_agent_system_prompt, get_agent_scope

        diff_mode = (context or {}).get("review_mode") == "diff" or language == "diff"
        scope = get_agent_scope("maintainability") if diff_mode else ""

        system_prompt = get_agent_system_prompt("maintainability", {
            "scope": scope,
            "language": language,
        })
        user_message = self._format_code_context(code, language, context)

        return await self._call_with_schema_validation(
            system_prompt,
            user_message,
            category="maintainability",
        )
