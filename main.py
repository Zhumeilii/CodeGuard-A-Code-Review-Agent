"""
智能代码审查助手 - 主入口

使用多 Agent 架构分析代码质量、潜在 bug、性能问题、安全漏洞和最佳实践。
"""
from dotenv import load_dotenv

from integrations.cli import cli
from version import __version__, __description__

# 加载环境变量
load_dotenv()

# 显示版本信息
print(f"{__description__}")
print(f"Version: {__version__}\n")


def main():
    """主函数"""
    # 使用 CLI 接口
    cli()


if __name__ == "__main__":
    main()
