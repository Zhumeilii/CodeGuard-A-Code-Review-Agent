"""
Lightweight tracing module for code-review-agent.

接口设计兼容 OpenTelemetry Span 概念，支持：
- 嵌套 Span（通过 contextvars 自动传播 parent）
- 结构化属性采集
- JSON 文件导出（可直接导入 Jaeger/Zipkin）
- 全局禁用开关（零开销）

Usage:
    from tracing import get_tracer

    tracer = get_tracer()
    async with tracer.async_span("review_pr", {"repo": "owner/repo"}) as span:
        ...
        span.set_attribute("findings_count", 5)
"""
from tracing.tracer import Span, Tracer, get_tracer

__all__ = ["Span", "Tracer", "get_tracer"]
