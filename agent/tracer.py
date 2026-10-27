# -*- coding: utf-8 -*-
"""
轻量 Span 链路追踪 — 用于审计 Agent 的工具调用链
RQagentPro 风格：context manager + 环形缓冲区 + JSON 导出
"""
import time
import json
from contextlib import contextmanager
from typing import List, Dict, Optional


class SpanTracer:
    """轻量级异步安全的 Span 追踪器"""

    def __init__(self, max_spans: int = 200):
        self.spans: List[Dict] = []
        self.max_spans = max_spans
        self._current_trace_id: Optional[str] = None

    @contextmanager
    def trace(self, trace_id: str = None):
        """创建一个追踪上下文"""
        import uuid
        self._current_trace_id = trace_id or str(uuid.uuid4())[:8]
        self.spans = []  # 重置当前 trace 的 spans
        try:
            yield self
        finally:
            self._current_trace_id = None

    @contextmanager
    def span(self, name: str, **attrs):
        """创建一个 Span（耗时自动计算）"""
        start = time.time()
        span_data = {
            "name": name,
            "attrs": attrs,
            "start_ts": start,
            "trace_id": self._current_trace_id,
        }
        try:
            yield span_data
        finally:
            span_data["duration_ms"] = round((time.time() - start) * 1000, 2)
            self._add_span(span_data)

    def _add_span(self, span: Dict):
        """添加 span 到缓冲区（环形）"""
        self.spans.append(span)
        if len(self.spans) > self.max_spans:
            self.spans = self.spans[-self.max_spans:]

    def add_event(self, name: str, **attrs):
        """添加一个瞬时事件（duration=0）"""
        self._add_span({
            "name": name,
            "attrs": attrs,
            "start_ts": time.time(),
            "duration_ms": 0,
            "trace_id": self._current_trace_id,
        })

    def to_dict(self) -> Dict:
        """导出为 dict（用于 API 响应）"""
        return {
            "trace_id": self._current_trace_id,
            "spans": self.spans,
            "total_spans": len(self.spans),
            "total_duration_ms": round(
                sum(s.get("duration_ms", 0) for s in self.spans), 2
            ),
        }

    def to_json(self) -> str:
        """导出为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def render_console(self) -> str:
        """控制台友好输出（树形结构）"""
        if not self.spans:
            return "(无追踪记录)"

        lines = [f"Trace: {self._current_trace_id}"]
        lines.append("-" * 60)
        for i, s in enumerate(self.spans):
            prefix = "├─" if i < len(self.spans) - 1 else "└─"
            duration = s.get("duration_ms", 0)
            name = s.get("name", "?")
            attrs_str = ""
            if s.get("attrs"):
                key_attrs = {k: str(v)[:40] for k, v in s["attrs"].items()
                            if k not in ("result", "data")}
                if key_attrs:
                    attrs_str = " " + json.dumps(key_attrs, ensure_ascii=False)
            lines.append(f"  {prefix} {name} ({duration}ms){attrs_str}")
        lines.append("-" * 60)
        total = sum(s.get("duration_ms", 0) for s in self.spans)
        lines.append(f"Total: {total}ms | {len(self.spans)} spans")
        return "\n".join(lines)

    def clear(self):
        """清空当前 trace 的 spans"""
        self.spans = []


# 模块级默认实例（方便快速使用）
_default_tracer = SpanTracer()


def get_tracer() -> SpanTracer:
    """获取默认追踪器"""
    return _default_tracer
