"""版本信息"""

__version__ = "1.0.0"
__author__ = "Code Review Agent Team"
__description__ = "智能代码审查助手 - 基于 Claude AI 的多 Agent 代码分析系统"

# 支持的模型
SUPPORTED_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
]

# 支持的语言
SUPPORTED_LANGUAGES = [
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "rust",
    "cpp",
    "c",
]

# 输出格式
OUTPUT_FORMATS = [
    "terminal",
    "json",
    "markdown",
]
