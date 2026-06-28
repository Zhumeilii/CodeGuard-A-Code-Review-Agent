"""
Trace 导出器 — 将 Span 数据写入 JSON 文件。

输出格式对齐 OpenTelemetry JSON Exporter，可直接导入 Jaeger/Zipkin。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

from tracing.tracer import Span, Tracer


class JsonFileExporter:
    """将 trace 数据导出为 JSON 文件"""

    def __init__(self, output_dir: str = None):
        self.output_dir = Path(
            output_dir or os.getenv("TRACE_OUTPUT_DIR", "traces")
        )

    def export(self, tracer: Tracer):
        """导出一次 trace 的所有 span 到 JSON 文件"""
        if not tracer.spans:
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"trace-{tracer.trace_id[:12]}.json"
        filepath = self.output_dir / filename

        trace_data = {
            "trace_id": tracer.trace_id,
            "service": tracer.service_name,
            "spans": [span.to_dict() for span in tracer.spans],
            "summary": tracer.get_summary(),
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, ensure_ascii=False, indent=2)

        return str(filepath)


def export_trace(tracer: Tracer) -> str | None:
    """便捷函数：导出当前 trace 到默认目录"""
    exporter = JsonFileExporter()
    return exporter.export(tracer)
