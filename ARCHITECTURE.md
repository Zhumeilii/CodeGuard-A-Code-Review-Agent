# 项目结构

```
code-review-agent/
├── agents/                      # Agent 模块
│   ├── __init__.py
│   ├── base_agent.py           # Agent 基类
│   ├── orchestrator.py         # 协调器
│   ├── quality_agent.py        # 代码质量分析
│   ├── bug_agent.py            # Bug 检测
│   ├── perf_agent.py           # 性能分析
│   ├── security_agent.py       # 安全扫描
│   └── practice_agent.py       # 最佳实践
│
├── integrations/               # 集成模块
│   ├── __init__.py
│   └── cli.py                  # 命令行接口
│
├── report/                     # 报告模块
│   ├── __init__.py
│   └── formatter.py            # 报告格式化器
│
├── tools/                      # 工具模块（可扩展）
│   └── __init__.py
│
├── main.py                     # 主入口
├── test_review.py              # 快速测试脚本
├── example_code.py             # 测试示例代码
├── requirements.txt            # 依赖列表
├── .env.example                # 环境变量示例
└── README.md                   # 项目文档
```

## 核心组件说明

### 1. BaseAgent (agents/base_agent.py)
所有专业 Agent 的基类，提供：
- Claude API 调用封装
- 响应解析
- 代码上下文格式化
- Adaptive thinking 支持

### 2. Orchestrator (agents/orchestrator.py)
协调器，负责：
- 管理所有专业 Agent
- 并发执行分析任务
- 汇总和格式化结果
- 文件和目录审查

### 3. 专业 Agents
每个 Agent 专注于特定领域：

- **QualityAgent**: 代码风格、命名、注释、可读性
- **BugAgent**: 逻辑错误、边界条件、资源泄漏
- **PerfAgent**: 算法复杂度、内存使用、I/O 优化
- **SecurityAgent**: 注入攻击、XSS、认证、加密
- **PracticeAgent**: 设计模式、SOLID、DRY、语言特性

### 4. ReportFormatter (report/formatter.py)
支持多种输出格式：
- Terminal: 彩色终端输出（使用 Rich）
- JSON: 结构化数据
- Markdown: 文档格式

### 5. CLI (integrations/cli.py)
命令行接口，提供：
- `review`: 审查单个文件
- `review-code`: 审查代码片段
- `review-dir`: 批量审查目录

## 工作流程

1. **接收请求** → CLI 或直接调用
2. **任务分发** → Orchestrator 创建 5 个 Agent 任务
3. **并发执行** → 所有 Agent 同时分析代码
4. **结果汇总** → Orchestrator 收集所有结果
5. **格式化输出** → ReportFormatter 生成报告

## 扩展点

### 添加新的 Agent
1. 继承 `BaseAgent`
2. 实现 `analyze()` 方法
3. 在 `Orchestrator` 中注册

### 添加新的输出格式
在 `ReportFormatter` 中添加新的格式化方法

### 添加工具支持
在 `tools/` 目录添加新的工具模块

### 集成到 CI/CD
使用 JSON 输出格式，解析结果并设置退出码

## 性能优化

- 使用 `asyncio.gather()` 并发执行所有 Agent
- 每个 Agent 独立运行，互不阻塞
- 支持流式输出（未来可扩展）

## 错误处理

- 每个 Agent 的错误独立处理
- 单个 Agent 失败不影响其他 Agent
- 提供详细的错误信息和堆栈跟踪
