"""
Redis-backed persistent task queue for code review jobs.

设计：
- 使用 Redis List (LPUSH/BRPOP) 作为 FIFO 队列
- processing hash 追踪正在执行的任务
- 失败重试 + dead letter queue
- Redis 不可用时提供 fallback 标志
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

QUEUE_NAME = "code-review:tasks"
PROCESSING_HASH = "code-review:processing"
DEAD_LETTER_QUEUE = "code-review:dead"


@dataclass
class ReviewTask:
    """审查任务数据结构"""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = "review"       # "review" | "push_review"
    repo_name: str = ""
    pr_number: Optional[int] = None
    branch: Optional[str] = None
    force_full: bool = False
    created_at: float = field(default_factory=time.time)
    retry_count: int = 0
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("TASK_MAX_RETRIES", "3"))
    )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "ReviewTask":
        return cls(**json.loads(data))


class TaskQueue:
    """Redis 持久化任务队列"""

    def __init__(self, redis_url: str = None, client=None):
        """
        Args:
            redis_url: Redis 连接 URL
            client: 预创建的 Redis 客户端（用于测试注入 fakeredis）
        """
        if client is not None:
            self.client = client
        else:
            import redis as _redis
            self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
            self.client = _redis.from_url(self.redis_url, decode_responses=True)

    def enqueue(self, task: ReviewTask) -> str:
        """入队一个审查任务，返回 task_id"""
        self.client.lpush(QUEUE_NAME, task.to_json())
        logger.info(f"任务入队: {task.task_id} type={task.task_type} repo={task.repo_name}")
        return task.task_id

    def dequeue(self, timeout: int = 5) -> Optional[ReviewTask]:
        """阻塞式出队，取出后记录到 processing hash"""
        result = self.client.brpop(QUEUE_NAME, timeout=timeout)
        if result is None:
            return None
        _, task_json = result
        task = ReviewTask.from_json(task_json)
        # 记录到 processing hash（task_id → task_json + 时间戳）
        self.client.hset(PROCESSING_HASH, task.task_id, task_json)
        return task

    def complete(self, task_id: str):
        """标记任务完成，从 processing hash 移除"""
        self.client.hdel(PROCESSING_HASH, task_id)
        logger.info(f"任务完成: {task_id}")

    def fail(self, task: ReviewTask):
        """任务失败：重试或进入 dead letter queue"""
        self.client.hdel(PROCESSING_HASH, task.task_id)
        task.retry_count += 1
        if task.retry_count < task.max_retries:
            logger.warning(
                f"任务失败，重新入队: {task.task_id} "
                f"(retry {task.retry_count}/{task.max_retries})"
            )
            self.client.lpush(QUEUE_NAME, task.to_json())
        else:
            logger.error(f"任务达到最大重试次数，进入 dead letter: {task.task_id}")
            self.client.lpush(DEAD_LETTER_QUEUE, task.to_json())

    def pending_count(self) -> int:
        """队列中等待处理的任务数"""
        return self.client.llen(QUEUE_NAME)

    def processing_count(self) -> int:
        """正在处理中的任务数"""
        return self.client.hlen(PROCESSING_HASH)

    def dead_letter_count(self) -> int:
        """死信队列中的任务数"""
        return self.client.llen(DEAD_LETTER_QUEUE)

    def health_check(self) -> bool:
        """检查 Redis 连通性"""
        try:
            return self.client.ping()
        except Exception:
            return False
