"""
Correctness Agent - 正确性与明显性能退化审查

负责检查：
- 逻辑错误
- 边界条件和空值处理
- 资源泄漏
- 明显复杂度退化、N+1 和重复 I/O
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class CorrectnessAgent(BaseAgent):
    """正确性审查 Agent"""

    def __init__(self, client, model):
        super().__init__(client, model, "correctness")

    async def analyze(self, code: str, language: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """检测正确性问题和明显性能退化"""
        from agents.prompt_loader import get_agent_system_prompt, get_agent_scope

        diff_mode = (context or {}).get("review_mode") == "diff" or language == "diff"
        scope = get_agent_scope("correctness") if diff_mode else ""

        system_prompt = get_agent_system_prompt("correctness", {
            "scope": scope,
            "language": language,
        })
        user_message = self._format_code_context(code, language, context)

        return await self._call_with_schema_validation(
            system_prompt,
            user_message,
            category="correctness",
        )
