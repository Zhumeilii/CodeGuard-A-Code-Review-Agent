"""
Bug Agent - 潜在 Bug 检测

负责检查：
- 逻辑错误
- 边界条件问题
- 空指针/未定义变量
- 资源泄漏
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class BugAgent(BaseAgent):
    """Bug 检测 Agent"""

    def __init__(self, client, model):
        super().__init__(client, model, "bug")

    async def analyze(self, code: str, language: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """检测潜在 Bug"""
        from agents.prompt_loader import get_agent_system_prompt, get_agent_scope

        diff_mode = (context or {}).get("review_mode") == "diff" or language == "diff"
        scope = get_agent_scope("bug") if diff_mode else ""

        system_prompt = get_agent_system_prompt("bug", {
            "scope": scope,
            "language": language,
        })

        user_message = self._format_code_context(code, language, context)

        result = await self._call_claude(system_prompt, user_message)

        return self._parse_result(result)

    def _parse_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析结果"""
        import json

        for text in result.get("text", []):
            try:
                if "```json" in text:
                    json_str = text.split("```json")[1].split("```")[0].strip()
                    return json.loads(json_str)
                elif "{" in text and "}" in text:
                    return json.loads(text)
            except:
                continue

        return {
            "raw_response": result,
            "bugs": [],
            "summary": "解析失败",
            "risk_level": "unknown",
        }
