#!/usr/bin/env python3
"""
测试多厂商模型支持
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agents.base_agent import BaseAgent, ModelProvider


def test_provider_detection():
    """测试提供商识别功能"""
    print("🧪 测试提供商识别功能\n")

    test_cases = [
        {
            "name": "Anthropic Claude",
            "env": {
                "LLM_BASE_URL": "https://api.anthropic.com/v1",
                "LLM_MODEL_ID": "claude-opus-4-6"
            },
            "expected": ModelProvider.ANTHROPIC
        },
        {
            "name": "阿里云通义千问",
            "env": {
                "LLM_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "LLM_MODEL_ID": "qwen-plus"
            },
            "expected": ModelProvider.ALIBABA
        },
        {
            "name": "OpenAI GPT",
            "env": {
                "LLM_BASE_URL": "https://api.openai.com/v1",
                "LLM_MODEL_ID": "gpt-4"
            },
            "expected": ModelProvider.OPENAI
        },
        {
            "name": "DeepSeek",
            "env": {
                "LLM_BASE_URL": "https://api.deepseek.com/v1",
                "LLM_MODEL_ID": "deepseek-chat"
            },
            "expected": ModelProvider.DEEPSEEK
        },
    ]

    passed = 0
    failed = 0

    for test in test_cases:
        # 设置环境变量
        for key, value in test["env"].items():
            os.environ[key] = value

        # 创建 BaseAgent 实例（使用 None 作为 client，仅测试识别功能）
        try:
            agent = BaseAgent(None, test["env"]["LLM_MODEL_ID"], "test")
            detected = agent.provider

            if detected == test["expected"]:
                print(f"✅ {test['name']}: {detected.value}")
                passed += 1
            else:
                print(f"❌ {test['name']}: 期望 {test['expected'].value}, 实际 {detected.value}")
                failed += 1
        except Exception as e:
            print(f"❌ {test['name']}: 异常 - {e}")
            failed += 1

        # 清理环境变量
        for key in test["env"].keys():
            os.environ.pop(key, None)

    print(f"\n📊 测试结果: {passed} 通过, {failed} 失败")
    return failed == 0


def test_thinking_support():
    """测试 thinking 支持检测"""
    print("\n🧪 测试 thinking 支持检测\n")

    test_cases = [
        ("Anthropic", ModelProvider.ANTHROPIC, True),
        ("阿里云", ModelProvider.ALIBABA, False),
        ("OpenAI", ModelProvider.OPENAI, False),
        ("DeepSeek", ModelProvider.DEEPSEEK, False),
    ]

    for name, provider, expected_support in test_cases:
        # 模拟不同的提供商
        os.environ["LLM_BASE_URL"] = f"https://{provider.value}.example.com"
        os.environ["LLM_MODEL_ID"] = f"{provider.value}-model"

        agent = BaseAgent(None, f"{provider.value}-model", "test")
        actual_support = agent.supports_thinking

        status = "✅" if actual_support == expected_support else "❌"
        print(f"{status} {name}: thinking 支持 = {actual_support}")

        # 清理
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_MODEL_ID", None)


def main():
    """运行所有测试"""
    print("=" * 60)
    print("多厂商模型支持测试")
    print("=" * 60 + "\n")

    success = test_provider_detection()
    test_thinking_support()

    print("\n" + "=" * 60)
    if success:
        print("✅ 所有测试通过！")
    else:
        print("❌ 部分测试失败")
    print("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
