# -*- coding: utf-8 -*-
"""会话状态管理（基础设施，不是工具）"""
import time
import re


class SessionManager:
    def __init__(self, max_history=20, timeout=1800):
        self.sessions = {}
        self.max_history = max_history
        self.timeout = timeout

    def get_or_create(self, session_id):
        self._cleanup()
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(session_id, self.max_history)
        s = self.sessions[session_id]
        s.last_active = time.time()
        return s

    def get(self, session_id):
        self._cleanup()
        return self.sessions.get(session_id)

    def reset(self, session_id):
        if session_id in self.sessions:
            del self.sessions[session_id]

    def _cleanup(self):
        now = time.time()
        expired = [sid for sid, s in self.sessions.items() if now - s.last_active > self.timeout]
        for sid in expired:
            del self.sessions[sid]


class Session:
    def __init__(self, session_id, max_history=20):
        self.session_id = session_id
        self.last_active = time.time()
        self.history = []
        self.max_history = max_history
        # 理解缓存
        self.last_understanding = None
        # 核心槽位（数组，支持多公司/多指标）
        self.slots = {
            "companies": [],
            "indicators": [],
            "years": [],
            "period": None,
            "top_k": None,
        }

    def update_slots(self, new_slots):
        """更新槽位，只覆盖非空值"""
        for k, v in new_slots.items():
            if v is not None and v != "":
                if k in ("companies", "indicators", "years") and isinstance(v, list):
                    if v:
                        self.slots[k] = v
                elif k in ("company", "indicator", "year"):
                    mapped = {"company": "companies", "indicator": "indicators", "year": "years"}
                    new_k = mapped[k]
                    if v not in self.slots.get(new_k, []):
                        self.slots[new_k] = self.slots.get(new_k, []) + [v]
                else:
                    self.slots[k] = v

    def get_context(self):
        """获取当前有效槽位"""
        return {k: v for k, v in self.slots.items() if v is not None}

    def add_turn(self, question, answer, slots, data=None, task_type=None):
        """保存一轮对话"""
        self.history.append({
            "question": question,
            "answer": answer[:500],
            "slots": dict(slots),
            "data": data,
            "indicator": slots.get("indicator", ""),
            "task_type": task_type,
            "time": time.time(),
        })
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def get_recent_history(self, n=3):
        """获取最近n轮对话"""
        return self.history[-n:]

    def get_last_understanding(self):
        return self.last_understanding

    def update_understanding(self, understanding):
        self.last_understanding = understanding
        if understanding.get("companies"):
            self.slots["companies"] = understanding["companies"]
        if understanding.get("indicators"):
            self.slots["indicators"] = understanding["indicators"]
        if understanding.get("years"):
            self.slots["years"] = understanding["years"]
        if understanding.get("period"):
            self.slots["period"] = understanding["period"]
        if understanding.get("top_k"):
            self.slots["top_k"] = understanding["top_k"]

    def get_last_turn(self):
        """获取上一轮对话"""
        return self.history[-1] if self.history else None

    def get_last_data(self):
        """获取上一轮的数据"""
        last = self.get_last_turn()
        return last.get("data") if last else None

    def get_last_slots(self):
        """获取上一轮的槽位"""
        last = self.get_last_turn()
        return last.get("slots", {}) if last else {}

    def get_last_task_type(self):
        """获取上一轮的任务类型"""
        last = self.get_last_turn()
        return last.get("task_type") if last else None

    def get_context_for_llm(self) -> dict:
        """
        获取 LLM 可用的上下文摘要（用于 ReAct 引擎的上下文注入）
        替代 FollowupResolver 的关键词匹配

        返回适合传给 PromptManager.build_context_hint() 的 dict
        """
        last_und = self.get_last_understanding() or {}
        last_turn = self.get_last_turn() or {}

        return {
            "company": (last_und.get("companies") or [""])[0]
                       or (self.slots.get("companies") or [""])[0]
                       or last_turn.get("slots", {}).get("company", ""),
            "indicator": (last_und.get("indicators") or [""])[0]
                         or (self.slots.get("indicators") or [""])[0]
                         or last_turn.get("slots", {}).get("indicator", ""),
            "year": (last_und.get("years") or [""])[0]
                    or (self.slots.get("years") or [""])[0]
                    or str(last_turn.get("slots", {}).get("year", "")),
            "answer_summary": (last_turn.get("answer", "") if last_turn else ""),
        }

    def get_history_for_llm(self, n: int = 3) -> list:
        """
        获取 LLM 格式的对话历史 [{"role":"user/assistant", "content":"..."}]

        用于 ReActAgent._build_messages()
        """
        history = []
        for turn in self.get_recent_history(n):
            history.append({"role": "user", "content": turn.get("question", "")})
            history.append({"role": "assistant", "content": turn.get("answer", "")[:300]})
        return history


_manager = None

def get_session_manager():
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager

# ============================================================
# B3: 从 ReAct 执行结果回填 understanding（纯函数，可单测）
# 解析本轮 tool_calls_log 里已确定的 company/indicator/year 槽位。
# 每条 sql_query 调用按优先级取"第一个非空来源"，不做跨来源拼接：
#   arguments.understanding 嵌套 > arguments 扁平 > result_parsed 规范化结果
#   > result_parsed.data 数据行兜底；多条调用之间做去重合并。
# 无可用信息时安全返回空列表。
# ============================================================

def _get_tool_calls_log(result):
    """兼容 AgentResult / dict / 原始 list 三种形态取 tool_calls_log，永不抛异常。"""
    if result is None:
        return []
    if isinstance(result, dict):
        log = result.get("tool_calls_log")
    else:
        log = getattr(result, "tool_calls_log", None)
    if isinstance(log, list):
        return log
    return result if isinstance(result, list) else []


def _to_text_list(value):
    """任意标量/列表值 → 去空去重的字符串列表。"""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out = []
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            item = str(item)
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in out:
            out.append(item)
    return out


def _pick_first_value(container, keys):
    """按 key 顺序取第一个非空值（空列表视为无值继续找）。"""
    for k in keys:
        v = container.get(k)
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)) and not v:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _pick_group(groups):
    """按优先级返回第一个非空候选组（整组采用，不跨来源混拼）。"""
    for group in groups:
        vals = _to_text_list(group)
        if vals:
            return vals
    return []


def _indicator_cn_labels(values):
    """指标名归一为中文标签（纯函数，永不抛异常）。

    接受英文列名（如 net_profit）、中文标签或概念组词的列表/标量，
    走 taxonomy 单真相源做 英文列名→中文标签 映射；无法映射的原样保留。
    """
    if values is None:
        return []
    try:
        from agent.taxonomy import en_to_cn
    except Exception:
        en_to_cn = None
    out = []
    for item in _to_text_list(values):
        mapped = en_to_cn(item) if en_to_cn else item
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def _row_indicator_labels(data_rows):
    """从 sql_query data 行中兜底解析指标列（英文列名 → 中文标签）。

    兼容 sql_tool 真实输出：行内除 stock_abbr/report_year 等元数据外，
    数值列名即该次查询实际执行的指标字段（如 net_profit）；仅当该列名
    存在于 taxonomy 注册表时才视为指标，避免把元数据/未知列误判为指标。
    纯函数，任何畸形输入都安全返回 []。
    """
    if not data_rows or not isinstance(data_rows, list):
        return []
    try:
        from agent.taxonomy import INDICATORS
    except Exception:
        INDICATORS = {}
    candidates = []
    for row in data_rows:
        if not isinstance(row, dict):
            continue
        for key in row:
            if isinstance(key, str) and key in INDICATORS and key not in candidates:
                candidates.append(key)
    return _indicator_cn_labels(candidates)


def _norm_year(value):
    """年份规范化: 2024 / "2024年" → "2024"；范围等无法简化的原样保留。"""
    s = value.strip() if isinstance(value, str) else str(value).strip()
    if not s:
        return ""
    pure = s[:-1] if s.endswith("年") else s
    if re.fullmatch(r"\d{4}", pure):
        return pure
    return s


def extract_understanding_from_result(result):
    """从 ReAct 结果解析本轮已确定的槽位，返回 understanding 结构。

    输入 result 可为 AgentResult（含 tool_calls_log）、等价 dict、
    tool_calls_log 原始 list 或 None。只取 sql_query 工具调用；
    输出 {"companies": [...], "indicators": [...], "years": [...]}，
    无可用信息时为三个空列表，调用方/update_understanding 均安全。
    指标统一归一为中文标签：sql_tool 在返回 JSON 顶层回写了补全后的
    understanding（含中文 indicators）；此处另兜底从 data 行英文列名
    映射中文（走 taxonomy），兼容旧版/其他工具输出的英文列名形态。
    """
    companies = []
    indicators = []
    years = []

    for tc in _get_tool_calls_log(result):
        if not isinstance(tc, dict):
            continue
        if tc.get("tool") != "sql_query":
            continue

        args = tc.get("arguments")
        args = args if isinstance(args, dict) else {}
        und = args.get("understanding")
        und = und if isinstance(und, dict) else {}
        parsed = tc.get("result_parsed")
        parsed = parsed if isinstance(parsed, dict) else {}
        data_rows = parsed.get("data")
        data_rows = data_rows if isinstance(data_rows, list) else []

        und_co = _pick_first_value(und, ("companies", "company"))
        args_co = _pick_first_value(args, ("companies", "company"))
        parsed_co = _pick_first_value(parsed, ("companies", "company"))
        company = _pick_group([
            und_co, args_co, parsed_co,
            [r.get("stock_abbr") for r in data_rows if isinstance(r, dict)],
        ])

        und_ind = _pick_first_value(und, ("indicators", "indicator", "metric"))
        args_ind = _pick_first_value(args, ("indicators", "indicator", "metric"))
        parsed_ind = _pick_first_value(
            parsed, ("display_metric", "indicators", "indicator", "metric"))
        indicator = _pick_group([
            _indicator_cn_labels(und_ind),
            _indicator_cn_labels(args_ind),
            _indicator_cn_labels(parsed_ind),
            _row_indicator_labels(data_rows),
        ])

        und_yr = _pick_first_value(und, ("years", "year"))
        args_yr = _pick_first_value(args, ("years", "year"))
        parsed_yr = _pick_first_value(parsed, ("years", "year"))
        year = _pick_group([
            und_yr, args_yr, parsed_yr,
            [r.get("report_year") for r in data_rows if isinstance(r, dict)],
        ])

        for v in company:
            if v not in companies:
                companies.append(v)
        for v in indicator:
            if v not in indicators:
                indicators.append(v)
        for v in year:
            v = _norm_year(v)
            if v and v not in years:
                years.append(v)

    return {
        "companies": companies,
        "indicators": indicators,
        "years": years,
    }
