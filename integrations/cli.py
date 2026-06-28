"""
CLI Interface - 命令行接口

提供友好的命令行交互
"""
import click
import asyncio
import os
import subprocess
from pathlib import Path
from rich.console import Console

console = Console()


@click.group()
def cli():
    """智能代码审查助手 - 使用 AI 分析代码质量、Bug、性能、安全和最佳实践"""
    pass


@cli.command()
@click.argument('file_path', type=click.Path(exists=True))
@click.option('--format', '-f', type=click.Choice(['terminal', 'json', 'markdown']), default='terminal', help='输出格式')
@click.option('--output', '-o', type=click.Path(), help='输出文件路径（仅用于 json/markdown 格式）')
@click.option('--model', '-m', default='claude-opus-4-6', help='使用的 Claude 模型')
def review(file_path, format, output, model):
    """审查单个代码文件"""
    from agents.orchestrator import Orchestrator

    async def run_review():
        orchestrator = Orchestrator(model=model)

        console.print(f"[cyan]正在审查文件: {file_path}[/cyan]")

        try:
            results = await orchestrator.review_file(file_path)

            if format == 'terminal':
                orchestrator.formatter.format_terminal(results)
            elif format == 'json':
                json_output = orchestrator.formatter.format_json(results)
                if output:
                    Path(output).write_text(json_output, encoding='utf-8')
                    console.print(f"[green]报告已保存到: {output}[/green]")
                else:
                    console.print(json_output)
            elif format == 'markdown':
                md_output = orchestrator.formatter.format_markdown(results)
                if output:
                    Path(output).write_text(md_output, encoding='utf-8')
                    console.print(f"[green]报告已保存到: {output}[/green]")
                else:
                    console.print(md_output)

        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")
            raise

    asyncio.run(run_review())


@cli.command("review-diff")
@click.argument("diff_path", required=False, type=click.Path(exists=True))
@click.option("--base", help="用于生成 diff 的 base ref，例如 main 或 base commit")
@click.option("--head", default="HEAD", show_default=True, help="用于生成 diff 的 head ref")
@click.option("--repo", help="仓库名或标识，用于提供审查上下文")
@click.option("--title", help="PR 标题，用于提供审查上下文")
@click.option("--body-file", type=click.Path(exists=True), help="包含 PR 描述的文件")
@click.option(
    "--agent",
    "agents",
    multiple=True,
    type=click.Choice(["correctness", "security", "maintainability", "policy"]),
    help="要运行的 Agent。默认: correctness security maintainability，存在规范库时额外运行 policy",
)
@click.option('--format', '-f', type=click.Choice(['terminal', 'json', 'markdown']), default='terminal', help='输出格式')
@click.option('--output', '-o', type=click.Path(), help='输出文件路径（仅用于 json/markdown 格式）')
@click.option('--model', '-m', default='claude-opus-4-6', help='使用的 Claude 模型')
def review_diff(diff_path, base, head, repo, title, body_file, agents, format, output, model):
    """审查 PR diff，只关注本次变更新增/修改行带来的问题"""
    from agents.orchestrator import Orchestrator

    async def run_review():
        if diff_path:
            diff = Path(diff_path).read_text(encoding='utf-8')
            source = diff_path
        elif base:
            proc = subprocess.run(
                ["git", "diff", f"{base}...{head}"],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                console.print(f"[red]生成 git diff 失败: {proc.stderr.strip()}[/red]")
                raise click.Abort()
            diff = proc.stdout
            source = f"git diff {base}...{head}"
        else:
            console.print("[red]错误: 请提供 diff 文件，或使用 --base/--head 从 git 生成 diff[/red]")
            raise click.Abort()

        if not diff.strip():
            console.print("[yellow]diff 为空，没有可审查的变更[/yellow]")
            return

        context = {
            "repo": repo,
            "pr_title": title,
            "pr_body": Path(body_file).read_text(encoding='utf-8') if body_file else None,
            "base_commit": base,
            "head_commit": head if base else None,
        }
        context = {k: v for k, v in context.items() if v}
        selected_agents = list(agents) if agents else None

        orchestrator = Orchestrator(model=model)
        console.print(f"[cyan]正在审查 PR diff: {source}[/cyan]")

        try:
            results = await orchestrator.review_diff(
                diff,
                context=context,
                enabled_agents=selected_agents,
            )

            if format == 'terminal':
                orchestrator.formatter.format_terminal(results)
            elif format == 'json':
                json_output = orchestrator.formatter.format_json(results)
                if output:
                    Path(output).write_text(json_output, encoding='utf-8')
                    console.print(f"[green]报告已保存到: {output}[/green]")
                else:
                    console.print(json_output)
            elif format == 'markdown':
                md_output = orchestrator.formatter.format_markdown(results)
                if output:
                    Path(output).write_text(md_output, encoding='utf-8')
                    console.print(f"[green]报告已保存到: {output}[/green]")
                else:
                    console.print(md_output)

        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")
            raise

    asyncio.run(run_review())


@cli.command()
@click.argument('code', type=str)
@click.option('--language', '-l', default='python', help='编程语言')
@click.option('--format', '-f', type=click.Choice(['terminal', 'json']), default='terminal')
def review_code(code, language, format):
    """直接审查代码片段"""
    from agents.orchestrator import Orchestrator

    async def run_review():
        orchestrator = Orchestrator()

        try:
            results = await orchestrator.review_code(code, language)

            if format == 'terminal':
                orchestrator.formatter.format_terminal(results)
            else:
                console.print(orchestrator.formatter.format_json(results))

        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")
            raise

    asyncio.run(run_review())


@cli.command()
@click.argument('directory', type=click.Path(exists=True))
@click.option('--pattern', '-p', default='*', help='文件匹配模式（默认匹配所有文件，递归搜索子目录）')
@click.option('--output-dir', '-o', type=click.Path(), help='报告输出目录')
@click.option('--model', '-m', default='claude-opus-4-6', help='使用的 Claude 模型')
def review_dir(directory, pattern, output_dir, model):
    """批量审查目录中的文件（递归搜索子目录）"""
    from agents.orchestrator import Orchestrator

    async def run_batch_review():
        orchestrator = Orchestrator(model=model)
        dir_path = Path(directory)
        _EXCLUDED_DIRS = {
            '.git', '.hg', '.svn',                          # 版本控制
            '__pycache__', '.mypy_cache', '.pytest_cache',  # Python 缓存
            'node_modules', '.npm',                          # JS 依赖
            '.venv', 'venv', 'env',                          # 虚拟环境
            'dist', 'build', '.tox',                         # 构建产物
        }
        files = [
            f for f in dir_path.rglob(pattern)
            if f.is_file()
            and not any(
                part.startswith('.') or part in _EXCLUDED_DIRS
                for part in f.relative_to(dir_path).parts
            )
        ]

        if not files:
            console.print(f"[yellow]未找到匹配 {pattern} 的文件[/yellow]")
            return

        console.print(f"[cyan]找到 {len(files)} 个文件[/cyan]")

        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

        for file in files:
            console.print(f"\n[cyan]审查: {file.name}[/cyan]")

            try:
                results = await orchestrator.review_file(str(file))

                if output_dir:
                    report_file = output_path / f"{file.stem}_review.md"
                    md_output = orchestrator.formatter.format_markdown(results)
                    report_file.write_text(md_output, encoding='utf-8')
                    console.print(f"[green]报告已保存: {report_file}[/green]")
                else:
                    orchestrator.formatter.format_terminal(results)

            except Exception as e:
                console.print(f"[red]审查失败: {e}[/red]")
                continue

    asyncio.run(run_batch_review())


@cli.command()
@click.argument('file_path', type=click.Path(exists=True))
@click.option(
    '--mode', '-m',
    type=click.Choice(['sandbox', 'full']),
    default='full',
    help='验证模式: sandbox=仅跑测试(省token), full=测试+LLM重审(默认)',
)
@click.option('--max-iterations', default=3, show_default=True, help='最大修复迭代次数')
@click.option('--output', '-o', type=click.Path(), help='将修复后的代码另存为此路径（不指定则直接覆盖原文件）')
@click.option('--model', default=None, help='使用的模型（默认读取环境变量）')
def fix(file_path, mode, max_iterations, output, model):
    """自动修复代码问题（Agentic Fix Loop）

    \b
    流程：审查 → 生成修复 → 验证 → 失败则 Reflect 重试 → 成功输出

    \b
    验证模式：
      sandbox  仅执行 pytest，节约 token，适合有完善测试的项目
      full     pytest + LLM 重审双重验证（默认，更全面）

    \b
    示例：
      python main.py fix example_code.py
      python main.py fix example_code.py --mode sandbox
      python main.py fix example_code.py --max-iterations 5 -o fixed.py
    """
    from agents.fix_loop import FixLoop
    from tools.models import VerifyMode

    async def run_fix():
        verify_mode = VerifyMode(mode)
        loop = FixLoop(mode=verify_mode, max_iterations=max_iterations, model=model)

        console.print(f"[cyan]🔧 Agentic Fix Loop 启动[/cyan]")
        console.print(f"[cyan]文件: {file_path}[/cyan]")
        console.print(f"[cyan]模式: {mode} | 最大迭代: {max_iterations}[/cyan]")

        try:
            state = await loop.run(file_path)

            # 如果指定了 output，将修复后代码另存
            if output and state.status.value == "success":
                from pathlib import Path as _Path
                _Path(output).write_text(state.current_code, encoding="utf-8")
                console.print(f"[green]修复后代码已保存到: {output}[/green]")

            console.print(loop.format_result(state))

        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")
            raise

    asyncio.run(run_fix())


@cli.command("review-pr")
@click.argument("pr_url")
@click.option("--token", envvar="GITHUB_TOKEN", help="GitHub Token（默认读取环境变量 GITHUB_TOKEN）")
@click.option("--model", default=None, help="使用的模型（默认读取环境变量）")
@click.option("--dry-run", is_flag=True, help="只打印结果，不发布 Comment 到 GitHub")
@click.option("--no-incremental", is_flag=True, help="强制执行全量审查，跳过增量审查逻辑")
def review_pr(pr_url, token, model, dry_run, no_incremental):
    """分析 GitHub PR 并发布 AI Review Comment

    \b
    PR URL 格式：
      https://github.com/owner/repo/pull/123
      owner/repo#123

    \b
    示例：
      python main.py review-pr https://github.com/owner/repo/pull/1
      python main.py review-pr owner/repo#1 --dry-run
      python main.py review-pr owner/repo#1 --token ghp_xxxxx
    """
    from integrations.pr_reviewer import PRReviewer

    if not token:
        console.print("[red]错误: 需要 GITHUB_TOKEN[/red]")
        console.print("请设置环境变量或使用 --token 参数")
        raise click.Abort()

    async def run_pr_review():
        reviewer = PRReviewer(github_token=token, model=model)

        console.print(f"[cyan]🔍 分析 PR: {pr_url}[/cyan]")
        if dry_run:
            console.print("[yellow]⚠️  dry-run 模式：不会发布 Comment[/yellow]")

        try:
            comment, url = await reviewer.review_pr_by_url(pr_url, dry_run=dry_run, incremental=not no_incremental)

            if dry_run:
                console.print("\n[cyan]─── Comment 预览 ───[/cyan]")
                console.print(comment)
                console.print("[cyan]─────────────────────[/cyan]")
            else:
                console.print(f"[green]✅ Comment 已发布: {url}[/green]")

        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")
            raise

    asyncio.run(run_pr_review())


@cli.command("webhook")
@click.option("--port", default=8080, show_default=True, help="监听端口")
@click.option("--secret", envvar="GITHUB_WEBHOOK_SECRET", help="Webhook Secret（用于验证签名）")
def webhook(port, secret):
    """启动 GitHub Webhook 服务

    \b
    功能：
      - 接收 GitHub pull_request 事件
      - 自动触发 PR 审查
      - 验证 HMAC-SHA256 签名

    \b
    配置步骤：
      1. 在 GitHub 仓库设置中添加 Webhook
      2. Payload URL: https://your-domain.com/webhook/github
      3. Content type: application/json
      4. Secret: 与 GITHUB_WEBHOOK_SECRET 一致
      5. 选择事件: Pull requests

    \b
    示例：
      python main.py webhook --port 8080
      python main.py webhook --port 8080 --secret my_secret_key
    """
    from integrations.webhook import is_usable_github_token, run_webhook_server

    if not is_usable_github_token(os.getenv("GITHUB_TOKEN")):
        console.print("[red]错误: 需要设置有效的 GITHUB_TOKEN 环境变量[/red]")
        console.print("请把 .env 中的 your_github_token_here 替换为真实 GitHub Token")
        raise click.Abort()

    try:
        run_webhook_server(port=port, webhook_secret=secret)
    except KeyboardInterrupt:
        console.print("\n[yellow]Webhook 服务已停止[/yellow]")
    except Exception as e:
        console.print(f"[red]错误: {e}[/red]")
        raise


@cli.command("worker")
@click.option("--concurrency", default=3, envvar="MAX_CONCURRENT_REVIEWS", show_default=True, help="最大并发审查数")
@click.option("--redis-url", envvar="REDIS_URL", default="redis://localhost:6379/0", show_default=True, help="Redis URL")
def worker_cmd(concurrency, redis_url):
    """启动后台 Worker 消费审查任务队列

    \b
    前置条件：
      - Redis 服务已启动
      - GITHUB_TOKEN 已配置

    \b
    配合 webhook 使用：
      终端 1: python main.py webhook --port 8080
      终端 2: python main.py worker --concurrency 3

    \b
    示例：
      python main.py worker
      python main.py worker --concurrency 5
      python main.py worker --redis-url redis://redis-host:6379/0
    """
    import asyncio
    from integrations.task_queue import TaskQueue
    from integrations.task_worker import ReviewWorker

    queue = TaskQueue(redis_url=redis_url)
    if not queue.health_check():
        console.print(f"[red]无法连接 Redis: {redis_url}[/red]")
        raise SystemExit(1)

    console.print(f"[green]Worker 启动[/green]")
    console.print(f"   Redis: {redis_url}")
    console.print(f"   并发数: {concurrency}")
    console.print(f"   队列待处理: {queue.pending_count()}")
    console.print()

    w = ReviewWorker(queue=queue, concurrency=concurrency)
    try:
        asyncio.run(w.start())
    except KeyboardInterrupt:
        console.print("\n[yellow]Worker 已停止[/yellow]")


if __name__ == '__main__':
    cli()
