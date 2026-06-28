#!/usr/bin/env python3
"""Tracing 模块单元测试"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from tracing.tracer import Span, Tracer, get_tracer
from tracing.exporter import JsonFileExporter


def test_span_basic_lifecycle():
    """Span 基本生命周期：创建 → 设置属性 → 结束"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    span = tracer.start_span("test_op", {"key": "value"})
    assert span.name == "test_op"
    assert span.attributes["key"] == "value"
    assert span.end_time is None

    span.set_attribute("extra", 42)
    tracer.end_span(span)

    assert span.end_time is not None
    assert span.status == "ok"
    assert span.duration_ms >= 0
    assert span.attributes["extra"] == 42


def test_span_nesting():
    """嵌套 Span 自动关联 parent"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    parent = tracer.start_span("parent")
    child = tracer.start_span("child")

    assert child.parent_id == parent.span_id
    assert parent.parent_id is None

    tracer.end_span(child)
    tracer.end_span(parent)
    assert len(tracer.spans) == 2


def test_span_context_manager():
    """同步上下文管理器自动 start/end"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    with tracer.span("sync_op", {"x": 1}) as s:
        s.set_attribute("y", 2)

    assert s.end_time is not None
    assert s.attributes == {"x": 1, "y": 2}
    assert s.status == "ok"


def test_async_span_context_manager():
    """异步上下文管理器"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    async def run():
        async with tracer.async_span("async_op") as s:
            s.set_attribute("result", "done")
        return s

    span = asyncio.run(run())
    assert span.status == "ok"
    assert span.attributes["result"] == "done"


def test_span_error_status():
    """异常时 span status 设为 error"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    try:
        with tracer.span("failing_op") as s:
            raise ValueError("test error")
    except ValueError:
        pass

    assert s.status == "error"


def test_disabled_tracer_returns_noop():
    """禁用时返回 NoOp span，不采集数据"""
    tracer = Tracer(enabled=False)
    tracer.new_trace()

    span = tracer.start_span("should_not_exist")
    span.set_attribute("key", "value")  # no-op
    tracer.end_span(span)

    assert len(tracer.spans) == 0


def test_add_event():
    """Span event 添加"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    with tracer.span("op") as s:
        s.add_event("cache_miss", {"key": "user:123"})

    assert len(s.events) == 1
    assert s.events[0]["name"] == "cache_miss"
    assert "timestamp" in s.events[0]


def test_get_summary():
    """Trace 摘要统计"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    with tracer.span("review_pr") as root:
        with tracer.span("llm.bug") as _:
            pass
        with tracer.span("llm.security") as _:
            pass

    summary = tracer.get_summary()
    assert summary["total_spans"] == 3
    assert summary["llm_calls"] == 2
    assert summary["errors"] == 0


def test_json_exporter():
    """JSON 导出生成有效文件"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    with tracer.span("test_review", {"repo": "owner/repo"}) as s:
        s.set_attribute("findings", 5)

    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = JsonFileExporter(output_dir=tmpdir)
        filepath = exporter.export(tracer)

        assert filepath is not None
        assert Path(filepath).exists()

        with open(filepath) as f:
            data = json.load(f)

        assert data["trace_id"] == tracer.trace_id
        assert data["service"] == "code-review-agent"
        assert len(data["spans"]) == 1
        assert data["spans"][0]["name"] == "test_review"
        assert data["spans"][0]["attributes"]["findings"] == 5


def test_span_to_dict():
    """Span 序列化为字典"""
    tracer = Tracer(enabled=True)
    tracer.new_trace()

    with tracer.span("op", {"model": "gpt-4"}) as s:
        pass

    d = s.to_dict()
    assert d["name"] == "op"
    assert d["attributes"]["model"] == "gpt-4"
    assert "duration_ms" in d
    assert d["status"] == "ok"


def test_new_trace_resets_spans():
    """new_trace() 重置 span 列表和 trace_id"""
    tracer = Tracer(enabled=True)
    old_id = tracer.new_trace()

    with tracer.span("first"):
        pass
    assert len(tracer.spans) == 1

    new_id = tracer.new_trace()
    assert new_id != old_id
    assert len(tracer.spans) == 0
