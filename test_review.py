#!/usr/bin/env python3
"""
快速测试脚本 - 测试代码审查助手
"""
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 检查 API Key
if not os.getenv("ANTHROPIC_API_KEY"):
    print("❌ 错误: 未设置 ANTHROPIC_API_KEY")
    print("请创建 .env 文件并添加你的 API Key:")
    print("  ANTHROPIC_API_KEY=your_api_key_here")
    exit(1)

from agents.orchestrator import Orchestrator


async def test_review():
    """测试代码审查功能"""
    print("=" * 60)
    print("智能代码审查助手 - 快速测试")
    print("=" * 60)

    # 创建 orchestrator
    orchestrator = Orchestrator()

    # 测试示例代码
    example_file = Path(__file__).parent / "example_code.py"

    if not example_file.exists():
        print(f"❌ 测试文件不存在: {example_file}")
        return

    print(f"\n📝 正在审查测试文件: {example_file.name}\n")

    try:
        # 执行审查
        results = await orchestrator.review_file(str(example_file))

        # 显示结果
        orchestrator.formatter.format_terminal(results)

        print("\n" + "=" * 60)
        print("✅ 测试完成！")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_review())
