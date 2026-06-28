"""
Base Agent - 所有专业 Agent 的基类
"""
from typing import Dict, List, Any, Optional, Union
from anthropic import AsyncAnthropic
import asyncio
import json
import os
import re
import time
from enum import Enum

from pydantic import ValidationError

from tools.models import AgentReviewResult, LLMOutputValidationError

# 全局 LLM API 并发限制，防止并发审查时同时发出过多 API 请求
_API_SEMAPHORE = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_LLM_CALLS", "10")))


class ModelProvider(Enum):
    """模型提供商枚举"""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    ALIBABA = "alibaba"
    DEEPSEEK = "deepseek"
    UNKNOWN = "unknown"


class BaseAgent:
    """Agent 基类"""

    def __init__(self, client: Union[AsyncAnthropic, Any], model: str, agent_type: str):
        self.client = client
        self.model = model
        self.agent_type = agent_type
        self.max_tokens = 16000

        # 识别模型提供商
        self.provider = self._detect_provider()

        # 根据提供商设置是否支持 thinking
        self.supports_thinking = self.provider == ModelProvider.ANTHROPIC
        self.review_confidence_threshold = float(os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "0.55"))
        self.llm_output_max_retries = int(os.getenv("LLM_OUTPUT_MAX_RETRIES", "2"))

    def _detect_provider(self) -> ModelProvider:
        """
        根据环境变量中的 base_url 和 model_id 识别模型提供商

        Returns:
            ModelProvider 枚举值
        """
        base_url = os.getenv("LLM_BASE_URL", "").lower()
        model_id = os.getenv("LLM_MODEL_ID", self.model).lower()

        # 根据 base_url 判断
        if "anthropic" in base_url or "claude" in base_url:
            return ModelProvider.ANTHROPIC
        elif "dashscope.aliyuncs.com" in base_url or "qwen" in model_id:
            return ModelProvider.ALIBABA
        elif "openai" in base_url or "gpt" in model_id:
            return ModelProvider.OPENAI
        elif "deepseek" in base_url or "deepseek" in model_id:
            return ModelProvider.DEEPSEEK

        # 如果没有配置 base_url，根据 model 名称判断
        if "claude" in self.model.lower():
            return ModelProvider.ANTHROPIC
        elif "gpt" in self.model.lower():
            return ModelProvider.OPENAI
        elif "qwen" in self.model.lower():
            return ModelProvider.ALIBABA
        elif "deepseek" in self.model.lower():
            return ModelProvider.DEEPSEEK

        return ModelProvider.UNKNOWN

    async def analyze(self, code: str, language: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        分析代码（子类需要实现）

        Args:
            code: 要分析的代码
            language: 编程语言
            context: 额外上下文

        Returns:
            分析结果字典
        """
        raise NotImplementedError("子类必须实现 analyze 方法")

    async def _call_claude(self, system_prompt: str, user_message: str, tools: List[Dict] = None) -> Dict[str, Any]:
        """
        调用 LLM API（支持多厂商），受全局并发 Semaphore 保护

        Args:
            system_prompt: 系统提示词
            user_message: 用户消息
            tools: 工具定义列表

        Returns:
            LLM 的响应
        """
        from tracing import get_tracer

        tracer = get_tracer()
        async with tracer.async_span(f"llm.{self.agent_type}", {
            "model": self.model,
            "provider": self.provider.value,
            "agent_type": self.agent_type,
        }) as span:
            async with _API_SEMAPHORE:
                t0 = time.time()
                messages = [{"role": "user", "content": user_message}]

                # 根据提供商选择不同的调用方式
                if self.provider == ModelProvider.ANTHROPIC:
                    result = await self._call_anthropic(system_prompt, messages, tools)
                else:
                    result = await self._call_openai_compatible(system_prompt, messages, tools)

                latency = (time.time() - t0) * 1000
                span.set_attribute("latency_ms", round(latency, 1))
                span.set_attribute("has_thinking", len(result.get("thinking", [])) > 0)
                span.set_attribute("text_blocks", len(result.get("text", [])))
                span.set_attribute("tool_uses", len(result.get("tool_uses", [])))
                return result

    async def _call_with_schema_validation(
        self,
        system_prompt: str,
        user_message: str,
        category: str,
        confidence_threshold: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Call the LLM and validate output against AgentReviewResult.

        Retries only when the response is not parseable JSON, fails schema
        validation, or leaves every finding below the required confidence.
        """
        threshold = self.review_confidence_threshold if confidence_threshold is None else confidence_threshold
        attempts = (self.llm_output_max_retries if max_retries is None else max_retries) + 1
        schema_prompt = self._schema_enforcement_prompt(category, threshold)
        prompt = system_prompt.rstrip() + "\n\n" + schema_prompt
        current_user_message = user_message
        last_error = ""

        for attempt in range(1, attempts + 1):
            result = await self._call_claude(prompt, current_user_message)
            raw_text = "\n".join(result.get("text", []))
            try:
                parsed = self._parse_review_result(raw_text, category, threshold)
                parsed["validation"] = {
                    "schema": "review-finding.v1",
                    "attempts": attempt,
                    "valid": True,
                }
                return parsed
            except LLMOutputValidationError as exc:
                last_error = str(exc)
                current_user_message = (
                    user_message
                    + "\n\n上一次输出未通过 JSON schema 校验。"
                    + f"\n错误: {last_error}"
                    + "\n请只返回一个严格 JSON 对象，不要 Markdown，不要额外解释。"
                    + "\n所有 finding 必须包含 category/type/severity/confidence/message/recommendation 字段。"
                )

        return {
            "findings": [],
            "summary": "LLM 输出未通过 schema 校验",
            "risk_level": "low",
            "score": None,
            "schema_version": "review-finding.v1",
            "confidence_threshold": threshold,
            "dropped_low_confidence": 0,
            "validation": {
                "schema": "review-finding.v1",
                "attempts": attempts,
                "valid": False,
                "error": last_error,
            },
        }

    def _parse_review_result(self, raw_text: str, category: str, confidence_threshold: float) -> Dict[str, Any]:
        data = self._extract_json_object(raw_text)
        if not isinstance(data, dict):
            raise LLMOutputValidationError("response is not a JSON object")

        data.setdefault("schema_version", "review-finding.v1")
        data.setdefault("summary", "")
        data.setdefault("risk_level", self._infer_risk_level(data.get("findings", [])))

        findings = data.get("findings", [])
        if not isinstance(findings, list):
            raise LLMOutputValidationError("findings must be a list")

        for item in findings:
            if isinstance(item, dict):
                item.setdefault("category", category)
                item.setdefault("confidence", 0.8)
                item.setdefault("type", "other")
                item.setdefault("severity", "medium")
                item.setdefault("recommendation", item.get("fix") or item.get("suggestion") or item.get("remediation") or "")

        try:
            review = AgentReviewResult.model_validate(data)
        except ValidationError as exc:
            raise LLMOutputValidationError(str(exc)) from exc

        retained = [
            finding for finding in review.findings
            if finding.confidence >= confidence_threshold
        ]
        dropped = len(review.findings) - len(retained)

        if review.findings and not retained:
            raise LLMOutputValidationError(
                f"all findings below confidence threshold {confidence_threshold}"
            )

        review.findings = retained
        review.confidence_threshold = confidence_threshold
        review.dropped_low_confidence = dropped
        if review.risk_level == "low" and retained:
            review.risk_level = self._infer_risk_level([finding.model_dump() for finding in retained])

        return review.model_dump()

    def _extract_json_object(self, text: str) -> Any:
        if not text:
            raise LLMOutputValidationError("empty response")

        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fenced:
            text = fenced.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise LLMOutputValidationError(f"invalid JSON: {exc}") from exc

        raise LLMOutputValidationError("no JSON object found")

    def _schema_enforcement_prompt(self, category: str, confidence_threshold: float) -> str:
        return f"""## 严格输出要求
只返回一个 JSON 对象，必须符合以下 schema，不要 Markdown 或额外说明：
{{
  "findings": [
    {{
      "category": "{category}",
      "type": "logic|boundary|injection|readability|other",
      "severity": "low|medium|high|critical",
      "confidence": 0.0到1.0,
      "line": 行号或 null,
      "existing_code": "从新增/修改后的代码逐字复制的最小连续片段；无法定位时为空字符串",
      "message": "问题描述",
      "evidence": "触发问题的具体证据",
      "impact": "可能影响",
      "recommendation": "修复或改进建议",
      "rule_id": null,
      "metadata": {{}}
    }}
  ],
  "summary": "一句话总结",
  "risk_level": "low|medium|high|critical",
  "score": 0到100或 null,
  "schema_version": "review-finding.v1"
}}
不要输出 confidence 低于 {confidence_threshold:.2f} 的 finding；不确定时返回空 findings。"""

    def _infer_risk_level(self, findings: List[Any]) -> str:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        max_level = "low"
        for item in findings or []:
            if not isinstance(item, dict):
                continue
            severity = item.get("severity", "low")
            if order.get(severity, 0) > order[max_level]:
                max_level = severity
        return max_level

    async def _call_anthropic(self, system_prompt: str, messages: List[Dict], tools: List[Dict] = None) -> Dict[str, Any]:
        """调用 Anthropic Claude API"""
        # 使用 adaptive thinking 进行深度分析
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=messages,
            thinking={"type": "adaptive"},
            tools=tools or [],
        )

        return self._extract_anthropic_response(response)

    async def _call_openai_compatible(self, system_prompt: str, messages: List[Dict], tools: List[Dict] = None) -> Dict[str, Any]:
        """调用 OpenAI 兼容的 API（阿里云、DeepSeek 等）"""
        # 将系统提示词添加到消息列表
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        # OpenAI 兼容接口不支持 thinking 参数
        kwargs = {
            "model": os.getenv("LLM_MODEL_ID", self.model),
            "max_tokens": self.max_tokens,
            "messages": full_messages,
        }

        # 如果有工具定义，添加 tools 参数
        if tools:
            kwargs["tools"] = tools

        response = await self.client.chat.completions.create(**kwargs)

        return self._extract_openai_response(response)

    def _extract_anthropic_response(self, response) -> Dict[str, Any]:
        """从 Anthropic Claude 响应中提取结果"""
        result = {
            "thinking": [],
            "text": [],
            "tool_uses": [],
        }

        for block in response.content:
            if block.type == "thinking":
                result["thinking"].append(block.thinking)
            elif block.type == "text":
                result["text"].append(block.text)
            elif block.type == "tool_use":
                result["tool_uses"].append({
                    "name": block.name,
                    "input": block.input,
                })

        return result

    def _extract_openai_response(self, response) -> Dict[str, Any]:
        """从 OpenAI 兼容响应中提取结果"""
        result = {
            "thinking": [],  # OpenAI 兼容接口不支持 thinking
            "text": [],
            "tool_uses": [],
        }

        # 获取第一个 choice
        if response.choices and len(response.choices) > 0:
            choice = response.choices[0]
            message = choice.message

            # 提取文本内容
            if message.content:
                result["text"].append(message.content)

            # 提取工具调用
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tool_call in message.tool_calls:
                    result["tool_uses"].append({
                        "name": tool_call.function.name,
                        "input": json.loads(tool_call.function.arguments),
                    })

        return result

    def _extract_response(self, response) -> Dict[str, Any]:
        """
        根据提供商类型提取响应（兼容旧代码）

        Args:
            response: API 响应对象

        Returns:
            统一格式的结果字典
        """
        if self.provider == ModelProvider.ANTHROPIC:
            return self._extract_anthropic_response(response)
        else:
            return self._extract_openai_response(response)

    def _format_code_context(self, code: str, language: str, context: Dict[str, Any] = None) -> str:
        """格式化代码上下文"""
        from agents.prompt_loader import get_user_message_template

        ctx = context or {}
        is_diff_review = ctx.get("review_mode") == "diff" or language == "diff"
        file_info = f"文件: {ctx.get('file_path', 'unknown')}\n" if ctx.get('file_path') else ""
        repo_info_lines = []
        for label, key in [
            ("仓库", "repo"),
            ("PR 标题", "pr_title"),
            ("Base", "base_commit"),
            ("Head", "head_commit"),
        ]:
            value = ctx.get(key)
            if value:
                repo_info_lines.append(f"{label}: {value}")
        if ctx.get("pr_body"):
            repo_info_lines.append(f"PR 描述:\n{ctx['pr_body']}")
        pr_info = "\n".join(repo_info_lines)

        repo_context = ctx.get("repo_context")
        repo_info = f"\n\n仓库上下文:\n{repo_context}" if repo_context else ""

        # 企业规范上下文（第一层 RAG 注入）
        policy_context = ctx.get("company_policy")
        policy_info = f"\n\n{policy_context}" if policy_context else ""

        mode = "diff_review" if is_diff_review else "code_review"
        return get_user_message_template(mode, {
            "file_info": file_info,
            "language": language,
            "pr_info": pr_info,
            "code": code,
            "repo_info": repo_info,
            "policy_info": policy_info,
        })
