#!/usr/bin/env python3
"""Task queue 单元测试（使用 fakeredis 模拟 Redis）"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import fakeredis

from integrations.task_queue import (
    DEAD_LETTER_QUEUE,
    PROCESSING_HASH,
    QUEUE_NAME,
    ReviewTask,
    TaskQueue,
)


def _make_queue():
    """创建使用 fakeredis 的 TaskQueue"""
    fake_client = fakeredis.FakeRedis(decode_responses=True)
    return TaskQueue(client=fake_client)


def test_enqueue_dequeue():
    """入队出队基本流程"""
    q = _make_queue()
    task = ReviewTask(task_type="review", repo_name="owner/repo", pr_number=42)
    q.enqueue(task)

    assert q.pending_count() == 1
    result = q.dequeue(timeout=1)
    assert result is not None
    assert result.task_id == task.task_id
    assert result.repo_name == "owner/repo"
    assert result.pr_number == 42
    assert q.pending_count() == 0
    assert q.processing_count() == 1


def test_complete_removes_from_processing():
    """完成后从 processing hash 移除"""
    q = _make_queue()
    task = ReviewTask(task_type="review", repo_name="owner/repo", pr_number=1)
    q.enqueue(task)
    dequeued = q.dequeue(timeout=1)

    assert q.processing_count() == 1
    q.complete(dequeued.task_id)
    assert q.processing_count() == 0


def test_fail_retries_then_dead_letter():
    """失败重试直到达到最大次数后进入 dead letter"""
    q = _make_queue()
    task = ReviewTask(task_type="review", repo_name="owner/repo", pr_number=1, max_retries=2)
    q.enqueue(task)

    # 第一次失败 → 重新入队
    dequeued = q.dequeue(timeout=1)
    q.fail(dequeued)
    assert q.pending_count() == 1
    assert q.dead_letter_count() == 0

    # 第二次失败 → 进入 dead letter
    dequeued = q.dequeue(timeout=1)
    q.fail(dequeued)
    assert q.pending_count() == 0
    assert q.dead_letter_count() == 1


def test_fifo_order():
    """队列保持 FIFO 顺序"""
    q = _make_queue()
    t1 = ReviewTask(task_type="review", repo_name="repo1", pr_number=1)
    t2 = ReviewTask(task_type="review", repo_name="repo2", pr_number=2)
    t3 = ReviewTask(task_type="review", repo_name="repo3", pr_number=3)
    q.enqueue(t1)
    q.enqueue(t2)
    q.enqueue(t3)

    d1 = q.dequeue(timeout=1)
    d2 = q.dequeue(timeout=1)
    d3 = q.dequeue(timeout=1)
    assert d1.task_id == t1.task_id
    assert d2.task_id == t2.task_id
    assert d3.task_id == t3.task_id


def test_dequeue_returns_none_when_empty():
    """队列空时 dequeue 返回 None"""
    q = _make_queue()
    result = q.dequeue(timeout=1)
    assert result is None


def test_health_check():
    """health check 对 fakeredis 返回 True"""
    q = _make_queue()
    assert q.health_check() is True


def test_task_serialization():
    """ReviewTask 序列化/反序列化"""
    task = ReviewTask(
        task_type="push_review",
        repo_name="org/project",
        branch="feature-x",
        force_full=True,
        retry_count=1,
    )
    json_str = task.to_json()
    restored = ReviewTask.from_json(json_str)
    assert restored.task_type == "push_review"
    assert restored.repo_name == "org/project"
    assert restored.branch == "feature-x"
    assert restored.force_full is True
    assert restored.retry_count == 1
