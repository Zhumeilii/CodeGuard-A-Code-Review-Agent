#!/usr/bin/env python3
"""LLM 输出 schema 校验、重试和置信度控制测试。"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import asyncio

from agents.base_agent import BaseAgent


class FakeSchemaAgent(BaseAgent):
    def __init__(self, responses):
        super().__init__(client=None, model="fake-model", agent_type="test")
        self.responses = list(responses)
        self.calls = 0
        self.llm_output_max_retries = 2
        self.review_confidence_threshold = 0.55

    async def _call_claude(self, system_prompt, user_message, tools=None):
        self.calls += 1
        return {"text": [self.responses.pop(0)]}


def test_schema_validation_retries_after_invalid_json():
    agent = FakeSchemaAgent([
        "not json",
        """
        {
          "findings": [{
            "category": "correctness",
            "type": "logic",
            "severity": "high",
            "confidence": 0.9,
            "line": 7,
            "existing_code": "return x",
            "message": "返回值错误",
            "evidence": "return x",
            "impact": "调用方得到错误结果",
            "recommendation": "返回修正后的值",
            "rule_id": null,
            "metadata": {}
          }],
          "summary": "发现 1 个问题",
          "risk_level": "high",
          "score": null,
          "schema_version": "review-finding.v1"
        }
        """,
    ])

    result = asyncio.run(agent._call_with_schema_validation("system", "user", "correctness"))

    assert agent.calls == 2
    assert result["validation"]["valid"] is True
    assert result["findings"][0]["category"] == "correctness"
    assert result["findings"][0]["confidence"] == 0.9


def test_low_confidence_findings_are_retried_and_dropped_on_failure():
    low_confidence = """
    {
      "findings": [{
        "category": "security",
        "type": "injection",
        "severity": "high",
        "confidence": 0.2,
        "line": 3,
        "existing_code": "query(user_input)",
        "message": "可能存在注入",
        "evidence": "query(user_input)",
        "impact": "可能被攻击",
        "recommendation": "使用参数化查询",
        "rule_id": "CWE-89",
        "metadata": {}
      }],
      "summary": "低置信度",
      "risk_level": "high",
      "score": null,
      "schema_version": "review-finding.v1"
    }
    """
    agent = FakeSchemaAgent([low_confidence, low_confidence, low_confidence])

    result = asyncio.run(agent._call_with_schema_validation("system", "user", "security"))

    assert agent.calls == 3
    assert result["validation"]["valid"] is False
    assert result["findings"] == []
    assert "confidence threshold" in result["validation"]["error"]


def test_schema_validation_adds_default_category_and_filters_mixed_confidence():
    agent = FakeSchemaAgent([
        """
        {
          "findings": [
            {
              "type": "readability",
              "severity": "medium",
              "confidence": 0.7,
              "line": 12,
              "message": "函数过长",
              "recommendation": "拆分函数"
            },
            {
              "type": "naming",
              "severity": "low",
              "confidence": 0.3,
              "line": 13,
              "message": "命名略含糊",
              "recommendation": "改名"
            }
          ],
          "summary": "可维护性问题",
          "risk_level": "medium",
          "score": 72
        }
        """,
    ])

    result = asyncio.run(agent._call_with_schema_validation("system", "user", "maintainability"))

    assert result["validation"]["valid"] is True
    assert len(result["findings"]) == 1
    assert result["findings"][0]["category"] == "maintainability"
    assert result["dropped_low_confidence"] == 1
