"""
Quality Agent - 代码质量分析

负责检查：
- 代码风格和格式
- 命名规范
- 注释完整性
- 代码可读性
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class QualityAgent(BaseAgent):
    """代码质量分析 Agent"""

    def __init__(self, client, model):
        super().__init__(client, model, "quality")

    async def analyze(self, code: str, language: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """分析代码质量"""
        from agents.prompt_loader import get_agent_system_prompt, get_agent_scope

        diff_mode = (context or {}).get("review_mode") == "diff" or language == "diff"
        scope = get_agent_scope("quality") if diff_mode else ""

        system_prompt = get_agent_system_prompt("quality", {
            "scope": scope,
            "language": language,
        })

        user_message = self._format_code_context(code, language, context)

        result = await self._call_claude(system_prompt, user_message)

        # 尝试解析 JSON 结果
        return self._parse_result(result)

    def _parse_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析 Agent 返回的结果"""
        import json

        # 尝试从 text 中提取 JSON
        for text in result.get("text", []):
            try:
                # 查找 JSON 代码块
                if "```json" in text:
                    json_str = text.split("```json")[1].split("```")[0].strip()
                    return json.loads(json_str)
                elif "{" in text and "}" in text:
                    # 尝试直接解析
                    return json.loads(text)
            except:
                continue

        # 如果无法解析，返回原始结果
        return {
            "raw_response": result,
            "issues": [],
            "summary": "解析失败",
            "score": 0,
        }
