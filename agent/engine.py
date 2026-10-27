# -*- coding: utf-8 -*-
"""
ReAct Agent 引擎 — 单次理解、动态工具调用
替换 FinanceAgent.query() 的双层 pipeline+agent 架构

RQagentPro 风格 ReAct 循环:
  用户问题 → 构建消息(系统Prompt + 历史 + 上下文注入)
           → [ReAct 循环]
              LLM 决定工具 → 执行 → 结果注入 → LLM 再决定
           → 输出校验 → 返回

核心改进:
  1. 不强制预处理 (pipeline.understand() 不再是必经之路)
  2. 不硬编码 INTENT_PLANS (LLM 自主选工具)
  3. 上下文注入替代关键词追问检测
  4. 单一路径, 无 _run_single / _run_single_with_und 重复
"""
import json
import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.llm import LLMClient, LLMResponse, ToolCall as LLMToolCall
from agent.tools.registry import get_tool_registry
from agent.prompt_manager import get_prompt_manager
from agent.tracer import SpanTracer, get_tracer


# ============================================================
# 同步桥接：event loop 复用（并发安全）
# ============================================================

_loop_guard = threading.Lock()
_shared_loop: Optional[asyncio.AbstractEventLoop] = None
_shared_loop_thread: Optional[threading.Thread] = None


def _get_shared_loop() -> asyncio.AbstractEventLoop:
    """懒创建并返回进程级复用的 event loop（常驻独立后台线程）。

    仅在"当前线程已有运行中的 event loop、无法再 asyncio.run()"的场景使用；
    run() 的常规同步路径（如 Flask 请求线程，当前线程无运行 loop）不经过这里。

    loop 必须在后台线程内部创建并运行（同一线程创建+运行），避免
    Windows ProactorEventLoop 等对"跨线程创建/运行"敏感的实现出问题。
    """
    global _shared_loop, _shared_loop_thread
    if _shared_loop is None:
        with _loop_guard:
            if _shared_loop is None:
                ready = threading.Event()
                holder = {}

                def _loop_worker():
                    loop = asyncio.new_event_loop()
                    holder["loop"] = loop
                    ready.set()
                    loop.run_forever()

                worker = threading.Thread(
                    target=_loop_worker,
                    name="agent-shared-loop",
                    daemon=True,
                )
                worker.start()
                ready.wait()
                _shared_loop = holder["loop"]
                _shared_loop_thread = worker
    return _shared_loop


def _run_coro_sync(coro) -> Any:
    """以同步方式执行协程，且不引发 event loop 冲突。

    规则：
      1. 当前线程无运行中的 event loop（Flask 默认线程 / 普通调用）：
         用 asyncio.run —— 仅在"当前线程无运行 loop"时才新建并运行 loop；
      2. 当前线程已有运行中的 event loop（已在 async 上下文）：
         不新建 loop，改为调度到进程级复用的单一 loop（独立后台线程）上
         执行并阻塞等待结果——跨线程安全，无 asyncio.run 冲突。
    """
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    shared_loop = _get_shared_loop()
    if running_loop is shared_loop:
        raise RuntimeError(
            "engine.run() 不允许在共享 event loop 所在线程内同步调用"
            "（会死锁）；异步代码请直接 await ReActAgent._run_async(...)"
        )
    return asyncio.run_coroutine_threadsafe(coro, shared_loop).result()


# ============================================================
# 数据结构
# ============================================================

@dataclass
class AgentResult:
    """ReAct Agent 运行结果"""
    answer: str = ""
    tool_calls_log: List[Dict] = field(default_factory=list)
    iterations: int = 0
    source: str = "agent"         # "agent" | "fallback" | "max_iterations" | "error"
    chart_html: Optional[str] = None
    sql_data: Optional[List[Dict]] = None
    session_id: str = ""
    trace: Optional[Dict] = None


def _tool_message_content(result_str: str, result_data: Any) -> str:
    """构造注入下一轮 LLM 的 tool 消息内容。

    chart_html 是仅供前端展示的 base64 大字段，不进入 LLM 上下文；
    统一截断为短预览，防止工具大结果造成上下文膨胀。
    """
    if isinstance(result_data, dict) and "chart_html" in result_data:
        summary = {k: v for k, v in result_data.items() if k != "chart_html"}
        result_str = json.dumps(summary, ensure_ascii=False)
    return result_str[:200]


# ============================================================
# ReAct Agent
# ============================================================

class ReActAgent:
    """单 Pass ReAct 财务分析 Agent"""

    def __init__(self, llm_client: LLMClient = None):
        from agent.config import MAX_STEPS
        self.max_iterations = MAX_STEPS
        self.history_turns = 3  # 保留最近3轮对话

        self.llm = llm_client or LLMClient()
        self.tool_registry = get_tool_registry()
        self.prompt_manager = get_prompt_manager()
        self.tracer = get_tracer()

        # 延迟导入 Pipeline（NER + Model2 工具的底层依赖）
        self._pipeline = None

    def _get_pipeline(self):
        """懒加载 pipeline（SQL 生成、字段匹配等核心能力仍需要）"""
        if self._pipeline is None:
            import sys
            import os
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "models", "model2_question_understanding"
            ))
            from pipeline import FinancialQAPipeline
            self._pipeline = FinancialQAPipeline()
        return self._pipeline

    # ================================================================
    # 主入口 (同步 — 兼容 Flask)
    # ================================================================

    def run(self, question: str, history: List[Dict] = None,
            session_id: str = "default",
            last_context: Dict = None) -> AgentResult:
        """
        同步主入口

        参数:
          question: 用户当前问题
          history: 对话历史 [{"role":"user/assistant", "content":"..."}]
          session_id: 会话 ID
          last_context: 上一轮的上下文（用于追问意图继承）
            {"company": "白云山", "indicator": "净利润", "year": "2024",
             "answer_summary": "白云山2024年净利润为45.2亿元"}

        返回:
          AgentResult

        Event loop 策略：当前线程无运行中的 loop 时走 asyncio.run；
        已在 async 上下文（有运行中的 loop）时调度到进程级复用的单一 loop，
        避免"asyncio.run() cannot be called from a running event loop"冲突。
        """
        return _run_coro_sync(
            self._run_async(question, history, session_id, last_context)
        )

    async def _run_async(self, question: str, history: List[Dict],
                         session_id: str, last_context: Dict) -> AgentResult:
        """异步实现"""

        with self.tracer.trace(session_id):
            # ---- Step 1: 构建消息 ----
            messages = self._build_messages(question, history, last_context)

            # ---- Step 2: ReAct 循环 ----
            tool_calls_log = []
            chart_html = None
            sql_data = None

            for iteration in range(1, self.max_iterations + 1):
                with self.tracer.span(f"react.iter_{iteration}") as span:
                    print(f"\n[ReAct] Iter {iteration}/{self.max_iterations}")

                    # LLM 调用（带工具 Schema）
                    try:
                        response = self.llm.chat_with_tools(
                            messages=messages,
                            tools=self.tool_registry.to_openai_schemas(),
                            tool_choice="auto",
                        )
                    except Exception as e:
                        print(f"  [ReAct] LLM 调用失败: {e}")
                        self.tracer.add_event("llm_error", error=str(e)[:200])
                        return AgentResult(
                            answer=f"系统处理请求时遇到问题：{e}",
                            tool_calls_log=tool_calls_log,
                            iterations=iteration,
                            source="error",
                            session_id=session_id,
                        )

                    # --- 情况 A: LLM 要调工具 ---
                    if response.finish_reason == "tool_calls" or response.tool_calls:
                        # 记录 assistant 消息
                        assistant_msg = self._build_assistant_msg(response)
                        messages.append(assistant_msg)

                        for tc in response.tool_calls:
                            print(f"  [ReAct] → 调用工具: {tc.name}"
                                  f"({json.dumps(tc.arguments, ensure_ascii=False)[:80]})")

                            with self.tracer.span(f"tool.{tc.name}",
                                                  args=str(tc.arguments)[:100]):
                                result_str = await self.tool_registry.execute(
                                    tc.name, **tc.arguments
                                )

                            # 解析结果
                            try:
                                result_data = json.loads(result_str)
                            except json.JSONDecodeError:
                                result_data = {"raw": result_str}
                            # json.loads("null") 返回 None，兜底
                            if result_data is None:
                                result_data = {"success": False, "error": "工具返回为空"}

                            tool_calls_log.append({
                                "iteration": iteration,
                                "tool": tc.name,
                                "arguments": tc.arguments,
                                "result_preview": result_str[:200],
                                "result_parsed": result_data,
                            })

                            # 收集图表和 SQL 数据
                            if tc.name == "chart" and not chart_html:
                                chart_html = result_data.get("chart_html")
                            if tc.name == "sql_query" and not sql_data:
                                sql_data = result_data.get("data", [])

                            # 注入工具结果（截断预览，chart_html 不进 LLM）
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": _tool_message_content(
                                    result_str, result_data
                                ),
                            })

                        continue  # 下一轮迭代

                    # --- 情况 B: LLM 直接返回答案 ---
                    if response.content:
                        print(f"  [ReAct] ✓ 最终回答 ({len(response.content)} 字符)")
                        self.tracer.add_event("final_answer",
                                              iterations=iteration,
                                              tools_used=len(tool_calls_log))

                        return AgentResult(
                            answer=response.content,
                            tool_calls_log=tool_calls_log,
                            iterations=iteration,
                            source="agent",
                            chart_html=chart_html,
                            sql_data=sql_data,
                            session_id=session_id,
                            trace=self.tracer.to_dict(),
                        )

                    # --- 情况 C: 空响应 ---
                    print(f"  [ReAct] ⚠ 空响应")
                    return AgentResult(
                        answer="抱歉，我暂时无法处理这个问题。请尝试换个问法。",
                        tool_calls_log=tool_calls_log,
                        iterations=iteration,
                        source="error",
                        session_id=session_id,
                    )

            # ---- 超出最大迭代 ----
            print(f"  [ReAct] ⚠ 达到最大迭代次数 ({self.max_iterations})")
            return AgentResult(
                answer="抱歉，处理您的问题时步骤较多，请尝试更具体的提问方式。",
                tool_calls_log=tool_calls_log,
                iterations=self.max_iterations,
                source="max_iterations",
                session_id=session_id,
            )

    # ================================================================
    # 消息构建
    # ================================================================

    def _build_messages(self, question: str, history: List[Dict],
                        last_context: Dict) -> List[Dict]:
        """
        构建 LLM 消息列表

        核心改进（替代 FollowupResolver）:
          不依赖关键词匹配，而是通过上下文注入让 LLM 语义理解追问。

          注入格式:
            [上下文提示：上一轮查询了白云山2024年净利润45.2亿元。
             当前问题可能是追问，请自动继承公司/年份/指标信息。]
        """
        with self.tracer.span("build_messages"):
            # 1. 系统 Prompt
            tool_descriptions = self.tool_registry.get_tool_descriptions()
            extra_context = ""

            # 2. 上下文注入（从 last_context 构建）
            if last_context:
                extra_context = self.prompt_manager.build_context_hint(
                    last_company=last_context.get("company", ""),
                    last_indicator=last_context.get("indicator", ""),
                    last_year=last_context.get("year", ""),
                    last_answer_summary=last_context.get("answer_summary", ""),
                )

            system_prompt = self.prompt_manager.build_system_prompt(
                tool_descriptions=tool_descriptions,
                extra_context=extra_context,
            )

            messages = [{"role": "system", "content": system_prompt}]

            # 3. 对话历史（最近 N 轮）
            if history:
                recent = history[-(self.history_turns * 2):]
                for msg in recent:
                    content = str(msg.get("content", ""))[:500]
                    messages.append({
                        "role": msg.get("role", "user"),
                        "content": content,
                    })

            # 4. 当前用户问题（如有上下文则在消息中标注）
            if last_context and last_context.get("company"):
                # 为短问题注入上下文提示
                if len(question) <= 15:
                    hint = (f"[提示：上一轮你查询了{last_context.get('company', '')}"
                            f"{last_context.get('year', '')}年的"
                            f"{last_context.get('indicator', '')}。"
                            f"请基于此理解用户当前问题，继承相关信息。]")
                    messages.append({
                        "role": "user",
                        "content": f"{question}\n\n{hint}"
                    })
                else:
                    messages.append({"role": "user", "content": question})
            else:
                messages.append({"role": "user", "content": question})

            print(f"  [ReAct] 消息构建: system + {len(messages)-1} 条，"
                  f"{'有' if last_context else '无'}上下文")
            return messages

    def _build_assistant_msg(self, response: LLMResponse) -> Dict:
        """将 LLMResponse 转为 OpenAI 格式的 assistant 消息"""
        tool_calls_list = []
        for tc in response.tool_calls:
            tool_calls_list.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            })

        msg = {
            "role": "assistant",
            "content": response.content,
            "tool_calls": tool_calls_list,
        }

        # DeepSeek reasoning_content
        if response.reasoning_content:
            msg["reasoning_content"] = response.reasoning_content

        return msg


# ============================================================
# 全局单例
# ============================================================

_agent_instance: Optional[ReActAgent] = None


def get_react_agent() -> ReActAgent:
    """获取全局 ReActAgent 单例"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = ReActAgent()
    return _agent_instance
