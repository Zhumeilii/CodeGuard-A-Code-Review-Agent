"""
Security Agent - 安全漏洞扫描

负责检查：
- SQL 注入
- XSS 跨站脚本
- CSRF 跨站请求伪造
- 敏感信息泄漏
- 不安全的加密
"""
from typing import Dict, Any
from .base_agent import BaseAgent


class SecurityAgent(BaseAgent):
    """安全漏洞扫描 Agent"""

    def __init__(self, client, model):
        super().__init__(client, model, "security")

    async def analyze(self, code: str, language: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """扫描安全漏洞"""
        from agents.prompt_loader import get_agent_system_prompt, get_agent_scope

        diff_mode = (context or {}).get("review_mode") == "diff" or language == "diff"
        scope = get_agent_scope("security") if diff_mode else ""

        system_prompt = get_agent_system_prompt("security", {
            "scope": scope,
            "language": language,
        })

        user_message = self._format_code_context(code, language, context)

        return await self._call_with_schema_validation(
            system_prompt,
            user_message,
            category="security",
        )

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
            "vulnerabilities": [],
            "summary": "解析失败",
            "security_score": 0,
            "risk_level": "unknown",
        }
