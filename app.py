# -*- coding: utf-8 -*-
"""Flask Web应用 — ReAct Agent 财务智能问答系统"""
import os
import sys
import json
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "models", "model2_question_understanding"))

from flask import Flask, render_template, request, jsonify
app = Flask(__name__)

# ===== Agent =====
_agent = None

def get_agent():
    global _agent
    if _agent is None:
        print("[Agent] Loading ReActAgent...")
        from agent.tools import register_all_tools
        register_all_tools()
        from agent.engine import ReActAgent
        _agent = ReActAgent()
        print(f"[Agent] ReActAgent Ready! (max {_agent.max_iterations} iterations)")
    return _agent


# ===== 数字格式化 (UnitConverter) =====
def format_smart_number(num, field_name=""):
    if num is None:
        return "N/A"
    try:
        from domain.units.converter import UnitConverter
        return UnitConverter.format_value(num, field_name)
    except Exception:
        try:
            n = float(num)
            if abs(n) >= 10000:
                return "{:.2f} 亿元".format(n / 10000)
            elif abs(n) >= 1:
                return "{:,.2f} 万元".format(n)
            else:
                return "{:.4f}".format(n)
        except Exception:
            return str(num)


# ===== 字段中文名 =====
def _load_field_cn():
    try:
        from agent.taxonomy import build_en_cn_map
        return build_en_cn_map()
    except Exception:
        pass
    return {
        "total_operating_revenue": "营业总收入", "net_profit": "净利润",
        "total_profit": "利润总额", "asset_total_assets": "总资产",
        "liability_total_liabilities": "总负债", "equity_total_equity": "净资产",
        "operating_cf_net_amount": "经营现金流",
    }

FIELD_CN = _load_field_cn()


# ===== HTML 模板工具 =====
def build_html_table(data, title=""):
    if not data or not isinstance(data, list):
        return ""
    val_keys = [k for k in data[0].keys()
                if k not in ('stock_abbr', 'report_year', 'report_period',
                             'stock_code', 'rank', 'note',
                             '_tbl_priority', '_rn', '_no_data')]
    parts = ['<div class="result-table-wrap"><table class="result-table">']
    parts.append(f'<caption>{title}</caption>')
    parts.append('<thead><tr><th>公司</th><th>年份</th>')
    for k in val_keys:
        parts.append(f'<th>{FIELD_CN.get(k, k)}</th>')
    parts.append('</tr></thead><tbody>')

    for row in data[:10]:
        name = row.get("stock_abbr", "")
        yr = row.get("report_year", "")
        rp = row.get("report_period", "")
        note = row.get("note", "")
        yl = f"{yr}年{' ' + rp if rp else ''}"
        parts.append(f'<tr><td>{name}</td><td>{yl}</td>')
        for k in val_keys:
            parts.append(f'<td>{format_smart_number(row.get(k), k)}</td>')
        parts.append('</tr>')
        if note:
            parts.append(
                f'<tr><td colspan="{len(val_keys) + 2}" class="note">{note}</td></tr>'
            )

    parts.append('</tbody></table></div>')
    return "".join(parts)


def build_report_html(report_md):
    if not report_md:
        return ""
    html = report_md
    html = html.replace("# ", "<h2>").replace("\n", "</h2>\n", 1) if html.startswith("#") else html
    html = html.replace("## ", "<h3>").replace("\n", "</h3>\n")
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    lines = html.split("\n")
    result = []
    in_table = False
    for line in lines:
        if "|" in line and "---" not in line:
            if not in_table:
                result.append('<table class="report-table">')
                in_table = True
            cells = [c.strip() for c in line.split("|") if c.strip()]
            tag = "th" if not any(c.replace(".", "").replace(",", "").replace("-", "").isdigit() for c in cells) else "td"
            result.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
        else:
            if in_table:
                result.append("</table>")
                in_table = False
            result.append(line)
    if in_table:
        result.append("</table>")
    return "<br>".join(result)


# ===== 核心处理 =====
def process_question(question, session_id="default"):
    try:
        agent = get_agent()
        from agent.session import get_session_manager, extract_understanding_from_result

        session_mgr = get_session_manager()
        session = session_mgr.get_or_create(session_id)

        # 获取上一轮上下文（替代 FollowupResolver 关键词匹配）
        last_context = session.get_context_for_llm() if session.history else None
        history = session.get_history_for_llm(3)

        result = agent.run(
            question,
            history=history,
            session_id=session_id,
            last_context=last_context,
        )

        # 记录本轮（B3: 从本轮 tool 调用解析 company/indicator/year 槽位并回填会话，
        # 替代 {"from": "react"} 占位，保证下一轮"那营收呢"这类追问能继承上轮上下文）
        answer = result.answer or "暂无数据"
        understanding = extract_understanding_from_result(result)
        if any(understanding.values()):
            session.update_understanding(understanding)
        session.add_turn(
            question, answer, dict(session.slots),
            data=result.sql_data,
            task_type=result.source,
        )

        # 从 tool_calls_log 提取数据
        sql_data = result.sql_data
        chart_html = result.chart_html
        report_md = None
        risk_alerts = None

        for tc in result.tool_calls_log:
            parsed = tc.get("result_parsed", {})
            if isinstance(parsed, dict):
                if tc.get("tool") == "report" and parsed.get("report"):
                    report_md = parsed["report"]
                if tc.get("tool") == "risk_analysis" and parsed.get("alerts"):
                    risk_alerts = parsed["alerts"]

        # 构建展示
        display_html = ""
        if report_md:
            display_html = build_report_html(report_md)
        elif sql_data:
            display_html = build_html_table(sql_data, title=question)

        rag_html = None
        if risk_alerts:
            parts = ['<div class="risk-section"><b>风险预警</b>']
            for a in risk_alerts:
                sev_class = {"高": "high", "中": "mid", "低": "low"}.get(a.get("severity", "低"), "low")
                parts.append(
                    f'<div class="risk-item {sev_class}">'
                    f'<span class="risk-sev">[{a.get("severity", "")}]</span> '
                    f'{a.get("rule", "")}：{a.get("detail", "")}</div>'
                )
            parts.append('</div>')
            rag_html = "".join(parts)

        return {
            "question": question,
            "task_type": result.source,
            "intent": result.source,
            "plan": " → ".join(tc["tool"] for tc in result.tool_calls_log),
            "confidence": 0.85,
            "answer": answer,
            "display_html": display_html,
            "rag_html_raw": rag_html,
            "chart": chart_html,
            "result_count": len(sql_data) if sql_data else 0,
            "trace": result.trace,
        }

    except Exception as e:
        import traceback
        print(f"[ERROR] process_question failed: {e}")
        traceback.print_exc()
        return {
            "question": question,
            "task_type": "error",
            "intent": "error",
            "plan": "",
            "confidence": 0,
            "answer": f"处理失败: {str(e)}",
            "display_html": "",
            "rag_html_raw": None,
            "chart": None,
            "result_count": 0,
        }


# ===== Flask 路由 =====
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/query", methods=["POST"])
def query():
    d = request.get_json()
    q = d.get("question", "").strip()
    if not q:
        return jsonify({"error": "empty"}), 400
    session_id = request.remote_addr or "default"
    return jsonify(process_question(q, session_id=session_id))


@app.route("/api/debug", methods=["POST"])
def debug_query():
    """诊断端点：展示 ReAct 追踪链路"""
    d = request.get_json()
    q = d.get("question", "").strip()
    if not q:
        return jsonify({"error": "empty"}), 400

    agent = get_agent()
    from agent.tracer import get_tracer

    result = agent.run(q, session_id="debug")
    tracer = get_tracer()

    return jsonify({
        "question": q,
        "answer": result.answer,
        "iterations": result.iterations,
        "source": result.source,
        "tool_calls": [
            {
                "iteration": tc["iteration"],
                "tool": tc["tool"],
                "arguments": tc.get("arguments", {}),
                "preview": tc.get("result_preview", "")[:200],
            }
            for tc in result.tool_calls_log
        ],
        "trace_tree": tracer.render_console(),
    })


@app.route("/api/reset", methods=["POST"])
def reset():
    from agent.session import get_session_manager
    session_id = request.remote_addr or "default"
    get_session_manager().reset(session_id)
    return jsonify({"status": "ok"})


@app.route("/api/quality")
def quality_api():
    """返回数据质量报告 JSON"""
    from data_quality_report import generate_report
    return jsonify(generate_report())


@app.route("/quality")
def quality_dashboard():
    """数据质量看板 HTML 页面"""
    from data_quality_report import generate_report, format_html
    report = generate_report()
    return format_html(report)


if __name__ == "__main__":
    print("=" * 50)
    print("  ReAct Agent 财务问答系统")
    print("  问答页面: http://127.0.0.1:7860")
    print("  质量看板: http://127.0.0.1:7860/quality")
    print("=" * 50)
    app.run(host="127.0.0.1", port=7860, debug=False)
