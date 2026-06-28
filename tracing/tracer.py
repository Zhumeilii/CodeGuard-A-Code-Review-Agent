"""
Lightweight OTel-compatible Tracer & Span implementation.

核心设计：
- Span 通过 contextvars 自动传播 parent（支持 async）
- Tracer 收集所有 span，结束时导出
- 禁用时所有操作为 no-op，零性能开销
"""
from __future__ import annotations

import contextvars
import os
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# ── 当前活跃 Span 上下文 ──────────────────────────────────
_current_span: contextvars.ContextVar[Optional["Span"]] = contextvars.ContextVar(
    "_current_span", default=None
)


@dataclass
class Span:
    """单个追踪 Span，对齐 OTel Span 接口"""

    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "ok"  # ok | error

    def set_attribute(self, key: str, value: Any):
        """设置 span 属性"""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Dict[str, Any] = None):
        """添加事件（类似 OTel Span Event）"""
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def end(self, status: str = "ok"):
        """结束 span"""
        self.end_time = time.time()
        self.status = status

    @property
    def duration_ms(self) -> float:
        """Span 持续时间（毫秒）"""
        if self.end_time is None:
            return (time.time() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> Dict[str, Any]:
        """导出为字典（兼容 JSON 序列化）"""
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 1),
            "attributes": self.attributes,
            "events": self.events,
            "status": self.status,
        }


class _NoOpSpan:
    """禁用时的空 Span，所有操作为 no-op"""

    def set_attribute(self, key: str, value: Any):
        pass

    def add_event(self, name: str, attributes: Dict[str, Any] = None):
        pass

    def end(self, status: str = "ok"):
        pass

    @property
    def duration_ms(self) -> float:
        return 0.0


_NOOP_SPAN = _NoOpSpan()


class Tracer:
    """轻量级 Tracer，管理 Span 生命周期和导出"""

    def __init__(
        self,
        service_name: str = "code-review-agent",
        enabled: bool = True,
    ):
        self.service_name = service_name
        self.enabled = enabled
        self._spans: List[Span] = []
        self._trace_id: str = uuid.uuid4().hex[:32]

    def new_trace(self) -> str:
        """开始新的 trace（新的 trace_id），返回 trace_id"""
        self._trace_id = uuid.uuid4().hex[:32]
        self._spans = []
        return self._trace_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def spans(self) -> List[Span]:
        return self._spans

    def start_span(self, name: str, attributes: Dict[str, Any] = None) -> Span:
        """创建并激活一个新 Span，自动关联 parent"""
        if not self.enabled:
            return _NOOP_SPAN  # type: ignore

        parent = _current_span.get()
        span = Span(
            name=name,
            trace_id=self._trace_id,
            parent_id=parent.span_id if parent else None,
            attributes=attributes or {},
        )
        _current_span.set(span)
        self._spans.append(span)
        return span

    def end_span(self, span: Span, status: str = "ok"):
        """结束 span，恢复 parent 上下文"""
        if not self.enabled:
            return
        span.end(status)
        # 恢复 parent（向上查找）
        if span.parent_id:
            parent = next(
                (s for s in self._spans if s.span_id == span.parent_id), None
            )
            _current_span.set(parent)
        else:
            _current_span.set(None)

    @contextmanager
    def span(self, name: str, attributes: Dict[str, Any] = None):
        """同步上下文管理器"""
        s = self.start_span(name, attributes)
        try:
            yield s
            self.end_span(s, "ok")
        except Exception:
            self.end_span(s, "error")
            raise

    @asynccontextmanager
    async def async_span(self, name: str, attributes: Dict[str, Any] = None):
        """异步上下文管理器"""
        s = self.start_span(name, attributes)
        try:
            yield s
            self.end_span(s, "ok")
        except Exception:
            self.end_span(s, "error")
            raise

    def get_summary(self) -> Dict[str, Any]:
        """获取当前 trace 的摘要统计"""
        if not self._spans:
            return {}
        root = next((s for s in self._spans if s.parent_id is None), None)
        return {
            "trace_id": self._trace_id,
            "total_spans": len(self._spans),
            "total_duration_ms": round(root.duration_ms, 1) if root else 0,
            "llm_calls": len([s for s in self._spans if s.name.startswith("llm.")]),
            "errors": len([s for s in self._spans if s.status == "error"]),
        }


# ── 全局单例 ──────────────────────────────────────────────
_tracer = Tracer(
    enabled=os.getenv("TRACING_ENABLED", "true").lower() == "true"
)


def get_tracer() -> Tracer:
    """获取全局 Tracer 实例"""
    return _tracer
