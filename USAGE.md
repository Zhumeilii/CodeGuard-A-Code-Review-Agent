# 使用指南

## 快速开始

### 1. 环境准备

```bash
# 安装依赖
pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env，添加你的 ANTHROPIC_API_KEY
```

### 2. 运行测试

```bash
# 使用测试脚本快速验证
python test_review.py
```

这会审查 `example_code.py` 并输出完整的分析报告。

## 命令行使用

### 审查单个文件

```bash
# 基本用法
python main.py review your_file.py

# 指定模型
python main.py review your_file.py --model claude-sonnet-4-6

# 输出为 JSON
python main.py review your_file.py --format json --output report.json

# 输出为 Markdown
python main.py review your_file.py --format markdown --output report.md
```

### 审查代码片段

```bash
# 直接审查代码
python main.py review-code "def hello(): print('world')" --language python

# 审查 JavaScript 代码
python main.py review-code "function test() { var x = 1; }" --language javascript
```

### 批量审查目录

```bash
# 审查所有 Python 文件
python main.py review-dir ./src --pattern "*.py" --output-dir ./reports

# 审查所有 JavaScript 文件
python main.py review-dir ./src --pattern "*.js" --output-dir ./reports

# 审查所有 TypeScript 文件
python main.py review-dir ./src --pattern "*.ts" --output-dir ./reports
```

## Python API 使用

### 基本用法

```python
import asyncio
from agents.orchestrator import Orchestrator

async def review_my_code():
    # 创建 orchestrator
    orchestrator = Orchestrator()

    # 审查文件
    results = await orchestrator.review_file("path/to/file.py")

    # 显示结果
    orchestrator.formatter.format_terminal(results)

# 运行
asyncio.run(review_my_code())
```

### 审查代码片段

```python
import asyncio
from agents.orchestrator import Orchestrator

async def review_code_snippet():
    orchestrator = Orchestrator()

    code = """
    def calculate(x, y):
        return x / y  # 潜在的除零错误
    """

    results = await orchestrator.review_code(code, language="python")

    # 获取 JSON 格式结果
    json_output = orchestrator.formatter.format_json(results)
    print(json_output)

asyncio.run(review_code_snippet())
```

### 自定义模型

```python
# 使用不同的 Claude 模型
orchestrator = Orchestrator(model="claude-sonnet-4-6")  # 更快但稍弱
orchestrator = Orchestrator(model="claude-opus-4-6")    # 最强但较慢
```

### 访问特定 Agent 的结果

```python
results = await orchestrator.review_file("file.py")

# 代码质量
quality = results["quality"]
print(f"质量评分: {quality['score']}")

# Bug 检测
bugs = results["bug"]
print(f"风险等级: {bugs['risk_level']}")

# 性能分析
perf = results["perf"]
print(f"性能评分: {perf['performance_score']}")

# 安全扫描
security = results["security"]
print(f"安全评分: {security['security_score']}")

# 最佳实践
practice = results["practice"]
print(f"实践评分: {practice['practice_score']}")
```

## 输出格式

### Terminal 格式（默认）

彩色终端输出，包含：
- 各维度评分
- 问题列表（表格形式）
- 修复建议
- 总体评价

### JSON 格式

结构化数据，适合程序处理：

```json
{
  "quality": {
    "score": 75,
    "summary": "...",
    "issues": [...]
  },
  "bug": {
    "risk_level": "medium",
    "bugs": [...]
  },
  "perf": {
    "performance_score": 80,
    "issues": [...]
  },
  "security": {
    "security_score": 90,
    "vulnerabilities": [...]
  },
  "practice": {
    "practice_score": 85,
    "recommendations": [...]
  }
}
```

### Markdown 格式

适合文档和报告：

```markdown
# 代码审查报告

## 📊 代码质量分析
**评分**: 75/100
...

## 🐛 潜在 Bug 检测
**风险等级**: medium
...
```

## 集成到 CI/CD

### GitHub Actions 示例

```yaml
name: Code Review

on: [pull_request]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2

      - name: Setup Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.9'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt

      - name: Run Code Review
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          python main.py review-dir ./src --format json --output report.json

      - name: Upload Report
        uses: actions/upload-artifact@v2
        with:
          name: code-review-report
          path: report.json
```

### GitLab CI 示例

```yaml
code_review:
  stage: test
  image: python:3.9
  script:
    - pip install -r requirements.txt
    - python main.py review-dir ./src --format markdown --output report.md
  artifacts:
    paths:
      - report.md
  only:
    - merge_requests
```

## 最佳实践

### 1. 选择合适的模型

- **claude-opus-4-6**: 最准确，适合重要代码审查
- **claude-sonnet-4-6**: 平衡速度和质量，适合日常使用

### 2. 批量审查优化

```python
# 对大型项目，分批处理
import glob

files = glob.glob("src/**/*.py", recursive=True)
batch_size = 10

for i in range(0, len(files), batch_size):
    batch = files[i:i+batch_size]
    # 处理这一批文件
```

### 3. 过滤结果

```python
# 只关注高严重性问题
high_severity_bugs = [
    bug for bug in results["bug"]["bugs"]
    if bug["severity"] in ["high", "critical"]
]
```

### 4. 自定义报告

```python
# 创建自定义报告格式
def create_summary(results):
    return {
        "total_issues": len(results["quality"]["issues"]),
        "critical_bugs": len([
            b for b in results["bug"]["bugs"]
            if b["severity"] == "critical"
        ]),
        "security_score": results["security"]["security_score"]
    }
```

## 故障排查

### API Key 错误

```
❌ 错误: 未设置 ANTHROPIC_API_KEY
```

**解决**: 确保 `.env` 文件存在且包含有效的 API Key

### 模型不可用

```
❌ 错误: Model not found
```

**解决**: 检查模型名称是否正确，使用 `claude-opus-4-6` 或 `claude-sonnet-4-6`

### 文件编码错误

```
❌ 错误: UnicodeDecodeError
```

**解决**: 确保文件使用 UTF-8 编码

### 超时错误

```
❌ 错误: Request timeout
```

**解决**:
- 增加超时时间（在 `.env` 中设置 `AGENT_TIMEOUT`）
- 使用更快的模型
- 分批处理大文件

## 性能提示

- 所有 Agent 并发执行，总时间约等于最慢的 Agent
- 典型审查时间：10-30 秒（取决于代码长度和模型）
- 使用 `claude-sonnet-4-6` 可以提速约 2-3 倍

## 成本估算

基于 Anthropic 定价（2026 年 5 月）：

- **claude-opus-4-6**: 约 $0.015 - $0.05 每次审查
- **claude-sonnet-4-6**: 约 $0.003 - $0.01 每次审查

实际成本取决于代码长度和复杂度。
