"""
Background worker that consumes review tasks from Redis queue.

启动方式：python main.py worker --concurrency 3
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Optional

from integrations.task_queue import ReviewTask, TaskQueue

logger = logging.getLogger(__name__)


class ReviewWorker:
    """从 Redis 队列消费审查任务的后台 Worker"""

    def __init__(
        self,
        queue: Optional[TaskQueue] = None,
        concurrency: int = None,
        github_token: str = None,
    ):
        self.queue = queue or TaskQueue()
        self.concurrency = concurrency or int(os.getenv("MAX_CONCURRENT_REVIEWS", "3"))
        self.github_token = github_token or os.getenv("GITHUB_TOKEN", "")
        self.semaphore = asyncio.Semaphore(self.concurrency)
        self.running = True
        self._active_tasks: set = set()

    async def start(self):
        """主循环：从队列取任务，受 Semaphore 并发限制"""
        logger.info(
            f"Worker 启动: concurrency={self.concurrency}, "
            f"queue_pending={self.queue.pending_count()}"
        )
        # 注册信号处理
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop)

        while self.running:
            await self.semaphore.acquire()
            if not self.running:
                self.semaphore.release()
                break

            # 阻塞式出队（在线程中执行避免阻塞 event loop）
            task = await asyncio.to_thread(self.queue.dequeue, timeout=2)
            if task is None:
                self.semaphore.release()
                continue

            # 创建异步任务执行审查
            t = asyncio.create_task(self._execute(task))
            self._active_tasks.add(t)
            t.add_done_callback(self._active_tasks.discard)

        # 等待正在执行的任务完成
        if self._active_tasks:
            logger.info(f"等待 {len(self._active_tasks)} 个活跃任务完成...")
            await asyncio.gather(*self._active_tasks, return_exceptions=True)
        logger.info("Worker 已停止")

    async def _execute(self, task: ReviewTask):
        """执行单个审查任务"""
        logger.info(f"开始执行任务: {task.task_id} type={task.task_type}")
        try:
            if task.task_type == "review":
                await self._run_review(task)
            elif task.task_type == "push_review": 
                await self._run_push_review(task)
            else:
                logger.warning(f"未知任务类型: {task.task_type}")
            self.queue.complete(task.task_id)
        except Exception as e:
            logger.error(f"任务执行失败: {task.task_id} error={e}", exc_info=True)
            self.queue.fail(task)
        finally:
            self.semaphore.release()

    async def _run_review(self, task: ReviewTask):
        """执行 PR 审查"""
        from integrations.pr_reviewer import PRReviewer
        reviewer = PRReviewer(github_token=self.github_token)
        _, url = await reviewer.review_pr(
            task.repo_name, task.pr_number, incremental=not task.force_full
        )
        logger.info(f"PR #{task.pr_number} 审查完成: {url}")

    async def _run_push_review(self, task: ReviewTask):
        """处理 push 事件：查找关联 PR 并审查"""
        from integrations.github_pr import GitHubPRProvider
        provider = GitHubPRProvider(self.github_token)
        pr_numbers = provider.find_open_prs_by_head_branch(task.repo_name, task.branch)
        if not pr_numbers:
            logger.info(f"push 分支 {task.repo_name}:{task.branch} 无关联 PR，跳过")
            return
        for pr_number in pr_numbers:
            sub_task = ReviewTask(
                task_type="review",
                repo_name=task.repo_name,
                pr_number=pr_number,
            )
            await self._run_review(sub_task)

    def stop(self):
        """优雅停止 Worker"""
        logger.info("收到停止信号，正在优雅停止...")
        self.running = False
