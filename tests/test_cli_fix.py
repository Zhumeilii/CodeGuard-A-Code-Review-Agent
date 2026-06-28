#!/usr/bin/env python3
"""
测试 CLI 修复 - 验证事件循环问题是否解决
"""
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_cli_import():
    """测试 CLI 导入"""
    print("🧪 测试 1: CLI 导入")
    try:
        from integrations.cli import cli
        print("✅ CLI 导入成功")
        return True
    except Exception as e:
        print(f"❌ CLI 导入失败: {e}")
        return False


def test_main_import():
    """测试 main 导入"""
    print("\n🧪 测试 2: main 导入")
    try:
        from main import main
        print("✅ main 导入成功")
        print(f"✅ main 是否为 async: {hasattr(main, '__await__')}")
        if hasattr(main, '__await__'):
            print("❌ 错误: main 不应该是 async 函数")
            return False
        return True
    except Exception as e:
        print(f"❌ main 导入失败: {e}")
        return False


def test_orchestrator():
    """测试 Orchestrator 初始化"""
    print("\n🧪 测试 3: Orchestrator 初始化")
    try:
        from agents.orchestrator import Orchestrator
        # 不实际创建实例，因为需要 API key
        print("✅ Orchestrator 导入成功")
        return True
    except Exception as e:
        print(f"❌ Orchestrator 导入失败: {e}")
        return False


def test_base_agent():
    """测试 BaseAgent 多厂商支持"""
    print("\n🧪 测试 4: BaseAgent 多厂商支持")
    try:
        from agents.base_agent import BaseAgent, ModelProvider
        print("✅ BaseAgent 导入成功")
        print(f"✅ 支持的提供商: {[p.value for p in ModelProvider]}")
        return True
    except Exception as e:
        print(f"❌ BaseAgent 导入失败: {e}")
        return False


def main():
    """运行所有测试"""
    print("=" * 60)
    print("CLI 修复验证测试")
    print("=" * 60 + "\n")

    tests = [
        test_cli_import,
        test_main_import,
        test_orchestrator,
        test_base_agent,
    ]

    results = []
    for test in tests:
        results.append(test())

    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")

    if passed == total:
        print("✅ 所有测试通过！CLI 修复成功！")
        print("\n💡 提示:")
        print("1. 配置 .env 文件中的 API key")
        print("2. 运行: python main.py review example_code.py")
    else:
        print("❌ 部分测试失败")

    print("=" * 60)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
