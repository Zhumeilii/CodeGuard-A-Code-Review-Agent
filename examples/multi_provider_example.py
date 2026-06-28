#!/usr/bin/env python3
"""
多厂商模型使用示例
演示如何使用不同的 LLM 提供商进行代码审查
"""
import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def example_anthropic():
    """使用 Anthropic Claude 进行代码审查"""
    print("\n" + "=" * 60)
    print("示例 1: 使用 Anthropic Claude")
    print("=" * 60)

    # 设置环境变量
    os.environ["ANTHROPIC_API_KEY"] = "your_anthropic_key"
    os.environ["MODEL"] = "claude-opus-4-6"

    from agents.orchestrator import Orchestrator

    # 创建协调器
    orchestrator = Orchestrator()

    # 示例代码
    code = """
def calculate_sum(numbers):
    total = 0
    for num in numbers:
        total = total + num
    return total
"""

    print(f"✅ 提供商: {orchestrator.agents['quality'].provider.value}")
    print(f"✅ 支持 thinking: {orchestrator.agents['quality'].supports_thinking}")
    print(f"✅ 模型: {orchestrator.model}")


async def example_alibaba():
    """使用阿里云通义千问进行代码审查"""
    print("\n" + "=" * 60)
    print("示例 2: 使用阿里云通义千问")
    print("=" * 60)

    # 设置环境变量
    os.environ["LLM_API_KEY"] = "your_alibaba_key"
    os.environ["LLM_MODEL_ID"] = "qwen-plus"
    os.environ["LLM_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    from agents.orchestrator import Orchestrator

    # 创建协调器
    orchestrator = Orchestrator()

    print(f"✅ 提供商: {orchestrator.agents['quality'].provider.value}")
    print(f"✅ 支持 thinking: {orchestrator.agents['quality'].supports_thinking}")
    print(f"✅ 模型: {os.getenv('LLM_MODEL_ID')}")


async def example_openai():
    """使用 OpenAI GPT 进行代码审查"""
    print("\n" + "=" * 60)
    print("示例 3: 使用 OpenAI GPT")
    print("=" * 60)

    # 设置环境变量
    os.environ["LLM_API_KEY"] = "your_openai_key"
    os.environ["LLM_MODEL_ID"] = "gpt-4"
    os.environ["LLM_BASE_URL"] = "https://api.openai.com/v1"

    from agents.orchestrator import Orchestrator

    # 创建协调器
    orchestrator = Orchestrator()

    print(f"✅ 提供商: {orchestrator.agents['quality'].provider.value}")
    print(f"✅ 支持 thinking: {orchestrator.agents['quality'].supports_thinking}")
    print(f"✅ 模型: {os.getenv('LLM_MODEL_ID')}")


async def example_deepseek():
    """使用 DeepSeek 进行代码审查"""
    print("\n" + "=" * 60)
    print("示例 4: 使用 DeepSeek")
    print("=" * 60)

    # 设置环境变量
    os.environ["LLM_API_KEY"] = "your_deepseek_key"
    os.environ["LLM_MODEL_ID"] = "deepseek-chat"
    os.environ["LLM_BASE_URL"] = "https://api.deepseek.com/v1"

    from agents.orchestrator import Orchestrator

    # 创建协调器
    orchestrator = Orchestrator()

    print(f"✅ 提供商: {orchestrator.agents['quality'].provider.value}")
    print(f"✅ 支持 thinking: {orchestrator.agents['quality'].supports_thinking}")
    print(f"✅ 模型: {os.getenv('LLM_MODEL_ID')}")


async def main():
    """运行所有示例"""
    print("=" * 60)
    print("多厂商模型使用示例")
    print("=" * 60)

    # 注意：这些示例仅展示配置方式，不会实际调用 API
    # 要实际运行，请替换为真实的 API Key

    await example_anthropic()
    await example_alibaba()
    await example_openai()
    await example_deepseek()

    print("\n" + "=" * 60)
    print("💡 提示:")
    print("1. 将示例中的 API Key 替换为真实的密钥")
    print("2. 在 .env 文件中配置相应的环境变量")
    print("3. 参考 docs/MULTI_PROVIDER_GUIDE.md 获取详细说明")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
