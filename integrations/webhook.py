"""
GitHub Webhook 服务 - FastAPI

功能：
- 接收 GitHub pull_request 事件
- 验证 HMAC-SHA256 签名
- 异步触发 PR 审查（不阻塞 webhook 响应）

启动：python main.py webhook --port 8080
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional
from urllib.parse import parse_qs

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from starlette.requests import Request
    _FASTAPI_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - handled when webhook mode is used
    FastAPI = HTTPException = Request = JSONResponse = None
    _FASTAPI_IMPORT_ERROR = exc

logger = logging.getLogger(__name__)

_REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
_WEBHOOK_VERSION = "2026-05-20-push-pr-review"
_PLACEHOLDER_GITHUB_TOKENS = {
    "your_github_token_here",
    "your_token_here",
    "github_token_here",
}


def is_usable_github_token(token: Optional[str]) -> bool:
    """Return False for empty values and documented placeholder tokens."""
    if not token:
        return False
    return token.strip() not in _PLACEHOLDER_GITHUB_TOKENS


def create_app(webhook_secret: Optional[str] = None, github_token: Optional[str] = None):
    """
    创建 FastAPI 应用

    Args:
        webhook_secret: GitHub Webhook Secret（用于验证签名）
        github_token: GitHub Token
    """
    if FastAPI is None:
        raise ImportError(
            "需要安装 fastapi 和 uvicorn：\n"
            "pip install fastapi uvicorn"
        ) from _FASTAPI_IMPORT_ERROR

    app = FastAPI(title="Code Review Agent Webhook", version="1.0.0")

    _secret = webhook_secret or os.getenv("GITHUB_WEBHOOK_SECRET", "")
    _token = github_token or os.getenv("GITHUB_TOKEN", "")

    # 初始化 Redis 任务队列（不可用时 fallback 到 asyncio.create_task）
    _queue = None
    try:
        from integrations.task_queue import ReviewTask, TaskQueue
        _queue = TaskQueue()
        if _queue.health_check():
            logger.info("Redis 任务队列已连接")
        else:
            logger.warning("Redis 不可达，回退到内存模式")
            _queue = None
    except Exception as e:
        logger.warning(f"Redis 队列初始化失败，回退到内存模式: {e}")
        _queue = None

    def _enqueue_or_fallback(task_type: str, repo_name: str, pr_number=None, branch=None, force_full=False):
        """入队任务，Redis 不可用时 fallback 到 asyncio.create_task"""
        if _queue is not None:
            from integrations.task_queue import ReviewTask
            task = ReviewTask(
                task_type=task_type,
                repo_name=repo_name,
                pr_number=pr_number,
                branch=branch,
                force_full=force_full,
                created_at=time.time(),
            )
            _queue.enqueue(task)
        else:
            # Fallback: 直接在进程内执行
            if task_type == "review":
                asyncio.create_task(_run_review(repo_name, pr_number, force_full=force_full))
            elif task_type == "push_review":
                asyncio.create_task(_run_push_review(repo_name, branch))

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        delivery = request.headers.get("X-GitHub-Delivery", "-")
        event = request.headers.get("X-GitHub-Event", "-")
        logger.info(
            "HTTP %s %s from=%s event=%s delivery=%s content_type=%s",
            request.method,
            request.url.path,
            request.client.host if request.client else "-",
            event,
            delivery,
            request.headers.get("Content-Type", "-"),
        )
        response = await call_next(request)
        logger.info(
            "HTTP %s %s -> %s event=%s delivery=%s",
            request.method,
            request.url.path,
            response.status_code,
            event,
            delivery,
        )
        return response

    def _verify_signature(body: bytes, signature: str) -> bool:
        """验证 GitHub Webhook HMAC-SHA256 签名"""
        if not _secret:
            logger.warning("GITHUB_WEBHOOK_SECRET 未配置，跳过签名验证")
            return True
        if not signature.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(
            _secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _parse_payload(body: bytes, content_type: str) -> dict:
        """解析 GitHub webhook payload，兼容 JSON 和 form-urlencoded。"""
        if "application/x-www-form-urlencoded" in content_type:
            form = parse_qs(body.decode("utf-8", errors="replace"))
            payload_values = form.get("payload")
            if not payload_values:
                raise ValueError("Missing form field: payload")
            return json.loads(payload_values[0])

        return json.loads(body)

    async def _run_review(repo_name: str, pr_number: int, force_full: bool = False):
        """后台执行 PR 审查"""
        from integrations.pr_reviewer import PRReviewer
        try:
            logger.info(f"开始审查 PR #{pr_number} ({repo_name}) force_full={force_full}")
            reviewer = PRReviewer(github_token=_token)
            comment, url = await reviewer.review_pr(repo_name, pr_number, incremental=not force_full)
            logger.info(f"PR #{pr_number} 审查完成: {url}")
        except Exception as e:
            logger.error(f"PR #{pr_number} 审查失败: {e}", exc_info=True)

    async def _run_push_review(repo_name: str, branch: str):
        """后台处理 push 事件：查找该分支对应的 open PR 并审查。"""
        from integrations.github_pr import GitHubPRProvider
        try:
            logger.info(f"开始处理 push 分支 {repo_name}:{branch}")
            pr_numbers = GitHubPRProvider(_token).find_open_prs_by_head_branch(repo_name, branch)
            if not pr_numbers:
                logger.info(f"push 分支 {repo_name}:{branch} 没有关联 open PR，跳过审查")
                return

            logger.info(f"push 分支 {repo_name}:{branch} 关联 PR: {pr_numbers}")
            for pr_number in pr_numbers:
                await _run_review(repo_name, pr_number)
        except Exception as e:
            logger.error(f"push 分支 {repo_name}:{branch} 审查失败: {e}", exc_info=True)

    @app.get("/health")
    async def health():
        """健康检查"""
        return {
            "status": "ok",
            "service": "code-review-agent",
            "webhook_version": _WEBHOOK_VERSION,
        }

    async def github_webhook(request: Request):
        """接收 GitHub Webhook 事件"""
        body = await request.body()
        delivery = request.headers.get("X-GitHub-Delivery", "-")

        # 验证签名
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(body, signature):
            logger.warning(f"Webhook 签名验证失败 delivery={delivery}")
            raise HTTPException(status_code=401, detail="Invalid signature")

        # 只处理 pull_request 事件
        event = request.headers.get("X-GitHub-Event", "")
        logger.info(f"收到 GitHub webhook event={event} delivery={delivery}")
        if event == "ping":
            return JSONResponse({"status": "pong", "webhook_version": _WEBHOOK_VERSION})

        if event == "push":
            try:
                payload = _parse_payload(body, request.headers.get("Content-Type", ""))
                repo_name = payload["repository"]["full_name"]
                ref = payload.get("ref", "")
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                logger.warning(f"push payload 解析失败 delivery={delivery}: {e}")
                raise HTTPException(status_code=400, detail="Invalid push payload")

            if not ref.startswith("refs/heads/"):
                logger.info(f"忽略非分支 push ref={ref} delivery={delivery}")
                return JSONResponse({"status": "ignored", "event": event, "ref": ref})
            if not is_usable_github_token(_token):
                logger.error("GITHUB_TOKEN 未配置或仍为占位符，无法执行审查")
                raise HTTPException(status_code=500, detail="GITHUB_TOKEN not configured")

            branch = ref.removeprefix("refs/heads/")
            _enqueue_or_fallback("push_review", repo_name, branch=branch)
            logger.info(f"已接受 push 分支 {repo_name}:{branch} 的 PR 审查任务 delivery={delivery}")
            return JSONResponse(
                {
                    "status": "accepted",
                    "event": event,
                    "repo": repo_name,
                    "branch": branch,
                    "webhook_version": _WEBHOOK_VERSION,
                },
                status_code=202,
            )

        if event == "issue_comment":
            try:
                payload = _parse_payload(body, request.headers.get("Content-Type", ""))
            except (ValueError, json.JSONDecodeError) as e:
                logger.warning(f"issue_comment payload 解析失败 delivery={delivery}: {e}")
                raise HTTPException(status_code=400, detail="Invalid payload")

            action = payload.get("action", "")
            if action != "created":
                return JSONResponse({"status": "ignored", "action": action})

            comment_body = payload.get("comment", {}).get("body", "")
            issue = payload.get("issue", {})

            # 只处理 PR 上的 comment（非普通 issue）
            if not issue.get("pull_request"):
                return JSONResponse({"status": "ignored", "reason": "not a PR comment"})

            # 解析 /review 命令
            force_full = "/review full" in comment_body
            has_review_cmd = "/review" in comment_body
            if not has_review_cmd:
                return JSONResponse({"status": "ignored", "reason": "no review command"})

            pr_number = issue.get("number")
            repo_name = payload["repository"]["full_name"]

            if not is_usable_github_token(_token):
                logger.error("GITHUB_TOKEN 未配置或仍为占位符，无法执行审查")
                raise HTTPException(status_code=500, detail="GITHUB_TOKEN not configured")

            _enqueue_or_fallback("review", repo_name, pr_number=pr_number, force_full=force_full)
            logger.info(f"已接受 /review 命令 PR #{pr_number} ({repo_name}) force_full={force_full}")
            return JSONResponse(
                {"status": "accepted", "command": "review", "force_full": force_full},
                status_code=202,
            )

        if event != "pull_request":
            return JSONResponse({"status": "ignored", "event": event})

        try:
            payload = _parse_payload(body, request.headers.get("Content-Type", ""))
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Webhook payload 解析失败 delivery={delivery}: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        action = payload.get("action", "")
        if action not in _REVIEW_ACTIONS:
            logger.info(f"忽略 pull_request action={action} delivery={delivery}")
            return JSONResponse({"status": "ignored", "action": action})

        try:
            pr_number = payload["pull_request"]["number"]
            repo_name = payload["repository"]["full_name"]
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"Missing field: {e}")

        if not is_usable_github_token(_token):
            logger.error("GITHUB_TOKEN 未配置或仍为占位符，无法执行审查")
            raise HTTPException(status_code=500, detail="GITHUB_TOKEN not configured")

        # 异步触发审查，立即返回 202 避免 GitHub 超时重试
        _enqueue_or_fallback("review", repo_name, pr_number=pr_number)
        logger.info(f"已接受 PR #{pr_number} ({repo_name}) 的审查任务 delivery={delivery}")

        return JSONResponse(
            {
                "status": "accepted",
                "pr": pr_number,
                "repo": repo_name,
                "webhook_version": _WEBHOOK_VERSION,
            },
            status_code=202,
        )

    # 使用 Starlette 原生路由，避免 FastAPI/Pydantic 将 webhook 入参误判为待校验字段而返回 422。
    app.add_route("/", github_webhook, methods=["POST"], include_in_schema=False)
    app.add_route("/webhook/github", github_webhook, methods=["POST"], include_in_schema=False)
    app.add_route("/webhook/github/", github_webhook, methods=["POST"], include_in_schema=False)
    logger.info(
        "Webhook route registered version=%s actions=%s",
        _WEBHOOK_VERSION,
        sorted(_REVIEW_ACTIONS),
    )

    return app


def run_webhook_server(port: int = 8080, webhook_secret: Optional[str] = None):
    """启动 Webhook 服务"""
    try:
        import uvicorn
    except ImportError:
        raise ImportError(
            "需要安装 uvicorn：\n"
            "pip install uvicorn"
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        force=True,
    )
    app = create_app(webhook_secret=webhook_secret)

    print(f"\n🚀 Webhook 服务启动")
    print(f"   地址: http://0.0.0.0:{port}")
    print(f"   版本: {_WEBHOOK_VERSION}")
    print(f"   Webhook 端点: POST /webhook/github (兼容: POST /)")
    print(f"   健康检查: GET /health")
    print(f"   签名验证: {'✅ 已启用' if webhook_secret or os.getenv('GITHUB_WEBHOOK_SECRET') else '⚠️  未配置（不安全）'}")
    print(f"\n   在 GitHub 仓库设置中配置 Webhook URL:")
    print(f"   https://your-domain.com/webhook/github\n")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
