# Code Review Agent

面向 GitHub PR 的多 Agent 代码审查系统。项目目标不是只对单个文件做一次 LLM 总结，而是模拟真实团队中的 review 流程：读取 PR diff，按 token 预算切分变更，结合仓库影响范围和企业规范知识库，并发运行多个审查 Agent，最后生成 PR 总结、证据链和可选的 inline comment。

## 真实工作流

```text
GitHub PR / local diff
        |
        v
PR metadata + changed files + previous review marker
        |
        v
Token-aware diff pipeline
  - 跳过 lock/generated/binary 文件
  - 解析 unified diff hunks
  - 标注新增行号
  - 按模型上下文窗口切分 chunk
        |
        v
Repo impact analysis
  - 建立轻量符号索引
  - 找到变更函数、调用方和依赖文件
  - 为 Agent 注入仓库级上下文
        |
        v
Multi-agent review
  - Correctness Agent
  - Security Agent
  - Maintainability Agent
  - Policy Reviewer Agent (有规范库时启用)
        |
        v
Schema validation
  - 统一 review-finding.v1 JSON schema
  - Pydantic 校验
  - 失败重试
  - 低置信度 finding 过滤
        |
        v
RAG evidence chain
  - 审查前检索企业规范
  - 审查后为 finding 挂载规范依据
  - Policy Reviewer 主动发现违规
        |
        v
Review output
  - PR summary comment
  - Critical/High inline comments
  - Markdown / JSON / terminal report
  - Trace JSON
        |
        v
Optional fix loop
  - 生成补丁
  - 应用与回滚
  - 语法检查
  - pytest 验证
  - LLM 重审
  - 失败反思重试
```

## 核心能力

- **PR 级审查**：支持 GitHub PR URL、local git diff、diff 文件输入，不只审查完整文件。
- **增量 review**：通过隐藏 review marker 记录上次 head SHA，只审查新增 commit 范围。
- **Token-aware diff 切分**：根据模型上下文窗口动态控制 chunk 数量和大小，避免大 PR 超上下文。
- **仓库影响分析**：提取函数、类、方法、调用关系和依赖文件，把变更影响范围提供给审查 Agent；Python 使用 AST，JS/TS 使用 tree-sitter 并支持 import/export、path alias、index module 和跨文件调用方解析。
- **多 Agent 并发**：Correctness、Security、Maintainability、Policy Reviewer 以 3+1 模式并发运行，单 Agent 失败不阻断整体审查。
- **严格输出治理**：所有 review Agent 输出统一 `review-finding.v1` JSON schema，支持 Pydantic 校验、失败重试和置信度过滤。
- **企业规范 RAG**：基于本地 policy YAML 建立 ChromaDB 索引，支持上下文注入、finding 证据挂载和主动规范审查。
- **GitHub 自动化**：支持 PR comment、inline comment、webhook 触发、Redis 队列和后台 worker。
- **自动修复闭环**：支持 review -> fix plan -> patch apply -> test/LLM verify -> reflection retry。
- **多模型提供商**：支持 Anthropic Claude，也支持 OpenAI-compatible provider，例如 Qwen、OpenAI、DeepSeek。
- **可观测性**：内置轻量 tracing，记录 PR review、orchestrator、LLM call 等 span。

## 适用场景

- 在本地审查某个 PR diff，快速发现新增代码中的 bug/security risk。
- 接入 GitHub webhook，在 PR opened/synchronize/comment command 时自动触发审查。
- 在企业规范固定的场景中，把安全、支付、测试、事故复盘等内部规则注入 code review。
- 对 LLM 生成的修复方案做最小化应用、测试验证和失败反思。

## 项目结构

```text
agents/
  base_agent.py          # LLM provider 适配、统一调用、tracing
  orchestrator.py        # 多 Agent 编排、三层 RAG、结果汇总
  *_agent.py             # Correctness/Security/Maintainability/Policy/Fix
integrations/
  cli.py                 # Click CLI
  diff_pipeline.py       # token-aware diff parser/chunker
  git_provider.py        # PR provider 抽象
  github_pr.py           # GitHub provider 实现
  pr_reviewer.py         # PR review 主流程
  webhook.py             # GitHub webhook 服务
  task_queue.py          # Redis-backed review queue
  task_worker.py         # 后台 worker
knowledge/
  policies/*.yaml        # 企业规范样例
  store.py               # ChromaDB policy store
  evidence.py            # finding evidence chain
tools/
  repo_index.py          # repo-level symbol index / JS/TS module resolution
  repo_map.py            # fix loop impact map
  patch_applier.py       # patch apply/rollback/syntax check
  sandbox.py             # pytest sandbox
tracing/
  tracer.py              # lightweight OTel-like tracer
  exporter.py            # trace JSON exporter
tests/
  test_*.py              # unit tests for core engineering modules
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置模型

复制 `.env.example` 为 `.env`，选择一种模型配置。

Anthropic:

```env
ANTHROPIC_API_KEY=your_anthropic_api_key
MODEL=claude-opus-4-6
```

OpenAI-compatible provider:

```env
LLM_API_KEY=your_api_key
LLM_MODEL_ID=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

### 3. 运行测试

```bash
pytest -q tests
```

当前核心测试覆盖 diff pipeline、provider abstraction、webhook、task queue、prompt loader、repo index、LLM 输出 schema、tracing 和 CLI import。

## 使用方式

### 审查本地 diff 文件

```bash
python main.py review-diff pr.diff --format markdown --output review.md
```

### 审查当前仓库分支差异

```bash
python main.py review-diff --base main --head HEAD
```

默认运行 `correctness`、`security`、`maintainability`；当企业规范库已加载时额外运行 `policy`。

```bash
python main.py review-diff pr.diff --agent correctness --agent security
```

LLM 输出会校验为统一 `review-finding.v1` JSON schema，并按置信度过滤 finding：

```bash
export REVIEW_CONFIDENCE_THRESHOLD=0.55
export LLM_OUTPUT_MAX_RETRIES=2
```

### 审查 GitHub PR

```bash
export GITHUB_TOKEN=ghp_xxx
python main.py review-pr owner/repo#123 --dry-run
```

发布 PR comment：

```bash
python main.py review-pr https://github.com/owner/repo/pull/123
```

强制全量审查，跳过增量逻辑：

```bash
python main.py review-pr owner/repo#123 --no-incremental
```

### 启动 webhook + worker

```bash
# Terminal 1
python main.py webhook --port 8080

# Terminal 2
python main.py worker --concurrency 3
```

Webhook 支持：

- `pull_request`: opened / synchronize / reopened / ready_for_review
- `push`: 查找对应 open PR 并触发审查
- `issue_comment`: 在 PR 下使用 `/review` 或 `/review full`

Redis 可用时任务进入持久队列；不可用时 webhook 回退到进程内异步任务。

### 单文件和目录审查

```bash
python main.py review path/to/file.py
python main.py review-dir ./src --pattern "*.py" --output-dir ./reports
python main.py review-code "def hello(): print('world')" --language python
```

### 自动修复

```bash
python main.py fix example_code.py
python main.py fix example_code.py --mode sandbox
python main.py fix example_code.py --max-iterations 5 --output fixed.py
```

`sandbox` 模式只跑测试验证；`full` 模式会额外执行 LLM 重审。

## 输出示例

PR comment 会包含：

- PR 基本信息和 review 模式
- 变更文件 / chunk / token 概览
- 仓库影响分析
- 按严重级别聚合的问题列表
- 企业规范证据链
- 被跳过文件和省略 chunk 说明
- 隐藏 review marker，用于后续增量审查

Markdown/JSON 输出适合接入 CI 或保存为审查报告；terminal 输出适合本地调试。

## 配置项

常用环境变量：

```env
# LLM
ANTHROPIC_API_KEY=...
MODEL=claude-opus-4-6
LLM_API_KEY=...
LLM_MODEL_ID=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MAX_CONCURRENT_LLM_CALLS=10

# GitHub
GITHUB_TOKEN=...
GITHUB_WEBHOOK_SECRET=...

# Incremental review
INCREMENTAL_MIN_COMMITS=0
INCREMENTAL_MIN_MINUTES=0
INCREMENTAL_REQUIRE_ALL_THRESHOLDS=false
INCREMENTAL_SKIP_MERGE_COMMITS=true

# Diff pipeline
DIFF_MAX_TOKENS_PER_CHUNK=6000
DIFF_MAX_CHUNKS=24
MODEL_CONTEXT_WINDOW=128000

# LLM output validation
REVIEW_CONFIDENCE_THRESHOLD=0.55
LLM_OUTPUT_MAX_RETRIES=2

# Worker / queue
REDIS_URL=redis://localhost:6379/0
MAX_CONCURRENT_REVIEWS=3
TASK_MAX_RETRIES=3

# Tracing
TRACING_ENABLED=true
TRACE_OUTPUT_DIR=traces
```

## 技术栈

- Python, asyncio, Click, Rich, Pydantic
- Anthropic SDK + OpenAI-compatible SDK
- ChromaDB + YAML policy knowledge base
- tree-sitter / AST fallback
- PyGithub, FastAPI, Uvicorn
- Redis task queue
- pytest

## 当前边界

- RAG 已接入审查流程，但还需要离线 benchmark 来量化 precision/recall、Recall@K 和误报率。
- 非 Python 语言已支持 JS/TS repo map 解析，但自动修复后的语法验证仍以 Python AST 为主。
- 自动修复适合小范围补丁，不适合大规模重构。
- 旧版 Bug/Quality/Performance/Practice Agent 文件仍保留作兼容代码，默认审查路径已切换到 3+1 架构。

## 面向后续迭代

- 增加公开可复现的 PR benchmark 和 baseline 对比。
- 增加 GitHub Actions，默认执行 lint + `pytest -q tests`。
- 增加成本、延迟、误报率统计面板。
- 强化 patch apply：支持 unified diff、AST transform 和多文件事务。

## License

MIT
