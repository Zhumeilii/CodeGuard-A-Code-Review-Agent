"""
Performance Agent - 性能优化分析

负责检查：
- 算法复杂度问题
- 不必要的循环和计算
- 内存使用优化
- 数据库查询优化
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class PerfAgent(BaseAgent):
    """性能优化分析 Agent"""

    def __init__(self, client, model):
        super().__init__(client, model, "performance")

    async def analyze(self, code: str, language: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """分析性能问题"""
        from agents.prompt_loader import get_agent_system_prompt, get_agent_scope

        diff_mode = (context or {}).get("review_mode") == "diff" or language == "diff"
        scope = get_agent_scope("perf") if diff_mode else ""

        system_prompt = get_agent_system_prompt("perf", {
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
            "issues": [],
            "summary": "解析失败",
            "performance_score": 0,
        }
