# -*- coding: utf-8 -*-
"""
数据质量报告生成器 — 可独立运行，也可被 Web 调用

用法:
  python data_quality_report.py           # 控制台输出
  python data_quality_report.py --json    # JSON 输出
  python data_quality_report.py --html    # HTML 输出

数据来源:
  1. data/logs/extraction/*.json — 提取日志
  2. MySQL report_metadata — 入库元数据
  3. MySQL pending_review — 异常记录
"""
import sys
import os
import json

def _cfg_password():
    """凭据来源：环境变量 > 项目内 config/config.yaml（该文件已 gitignore，不入库）"""
    import os as _os
    v = _os.environ.get("DB_PASSWORD")
    if v:
        return v
    try:
        import yaml
        d = _os.path.dirname(_os.path.abspath(__file__))
        for _ in range(4):
            p = _os.path.join(d, "config", "config.yaml")
            if _os.path.exists(p):
                cfg = yaml.safe_load(open(p, encoding="utf-8")) or {}
                return (cfg.get("database") or {}).get("password", "")
            d = _os.path.dirname(d)
    except Exception:
        pass
    return ""


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def load_log_stats():
    """从提取日志获取统计"""
    from pdf_extractor.extraction_logger import load_all_logs, compute_success_rate
    logs = load_all_logs()
    return compute_success_rate(logs)


def load_db_stats():
    """从 MySQL 获取统计"""
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host="localhost", port=3306, database="finance_data",
            user="root", password=_cfg_password(), connection_timeout=5
        )
        c = conn.cursor(dictionary=True)

        # report_metadata 汇总
        c.execute("SELECT COUNT(*) as cnt FROM report_metadata")
        total_reports = c.fetchone()["cnt"]

        c.execute("""
            SELECT
                COUNT(*) as cnt,
                SUM(fields_total) as total_fields,
                SUM(fields_valid) as valid_fields,
                SUM(fields_flagged) as flagged_fields,
                AVG(extract_confidence) as avg_confidence
            FROM report_metadata
        """)
        meta_stats = c.fetchone()

        # pending_review 汇总
        c.execute("SELECT COUNT(*) as cnt FROM pending_review WHERE status = 'pending'")
        pending = c.fetchone()["cnt"]

        c.execute("SELECT COUNT(*) as cnt FROM pending_review")
        total_flags = c.fetchone()["cnt"]

        # 各表行数
        table_rows = {}
        for tbl in ["income_sheet", "balance_sheet", "cash_flow_sheet",
                     "core_performance_indicators_sheet"]:
            try:
                c.execute("SELECT COUNT(*) as cnt FROM %s" % tbl)
                table_rows[tbl] = c.fetchone()["cnt"]
            except Exception:
                table_rows[tbl] = 0

        # 各字段 NULL 率
        field_null_rates = {}
        for tbl, fields in [
            ("income_sheet", ["total_operating_revenue", "net_profit", "total_profit"]),
            ("balance_sheet", ["asset_total_assets", "liability_total_liabilities",
                               "equity_total_equity"]),
            ("cash_flow_sheet", ["operating_cf_net_amount", "net_cash_flow"]),
            ("core_performance_indicators_sheet", ["eps", "roe_weighted_excl_non_recurring",
                                                    "gross_profit_margin"]),
        ]:
            for fld in fields:
                try:
                    c.execute("SELECT COUNT(*) as cnt FROM %s WHERE %s IS NULL" % (tbl, fld))
                    null_count = c.fetchone()["cnt"]
                    field_null_rates[f"{tbl}.{fld}"] = {
                        "null_count": null_count,
                        "total": table_rows.get(tbl, 0),
                    }
                except Exception:
                    pass

        c.close()
        conn.close()

        return {
            "total_reports": total_reports,
            "total_fields": meta_stats["total_fields"] or 0,
            "valid_fields": meta_stats["valid_fields"] or 0,
            "flagged_fields": meta_stats["flagged_fields"] or 0,
            "avg_confidence": round(meta_stats["avg_confidence"] or 0, 1),
            "field_pass_rate": round(
                (meta_stats["valid_fields"] or 0) / max(
                    (meta_stats["total_fields"] or 1), 1
                ) * 100, 1
            ),
            "pending_flags": pending,
            "total_flags": total_flags,
            "table_rows": table_rows,
            "field_null_rates": field_null_rates,
        }
    except Exception as e:
        return {"error": str(e), "source": "mysql_unavailable"}


def generate_report() -> dict:
    """生成完整数据质量报告"""
    report = {
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "extraction": load_log_stats(),
        "database": load_db_stats(),
    }
    return report


def format_console(report: dict) -> str:
    """格式化为控制台输出"""
    lines = []
    lines.append("=" * 60)
    lines.append("  数据质量报告 — %s" % report["generated_at"])
    lines.append("=" * 60)

    # 提取统计
    ext = report.get("extraction", {})
    lines.append("\n## 一、PDF 提取统计")
    lines.append("  PDF 总数:        %d" % ext.get("total_pdfs", 0))
    lines.append("  提取成功:        %d" % ext.get("successful_pdfs", 0))
    lines.append("  PDF 成功率:      %.1f%%" % ext.get("pdf_success_rate", 0))
    lines.append("  字段总数:        %d" % ext.get("total_fields", 0))
    lines.append("  平均字段/PDF:    %.1f" % ext.get("avg_fields_per_pdf", 0))
    lines.append("  平均映射置信度:  %.1f" % ext.get("field_confidence_avg", 0))

    # 按表统计
    by_table = ext.get("by_table", {})
    if by_table:
        lines.append("\n  各表提取成功率:")
        for tbl, stats in sorted(by_table.items()):
            cn = {"income_sheet": "利润表", "balance_sheet": "资产负债表",
                  "cash_flow_sheet": "现金流量表",
                  "core_performance_indicators_sheet": "核心指标"}.get(tbl, tbl)
            lines.append("    %-12s: %.1f%% (%d/%d) 字段=%d" % (
                cn, stats.get("rate", 0), stats.get("found", 0),
                stats.get("total", 0), stats.get("total_fields", 0)))

    # 按年统计
    by_year = ext.get("by_year", {})
    if by_year:
        lines.append("\n  按年份成功率:")
        for yr in sorted(by_year.keys()):
            ys = by_year[yr]
            lines.append("    %s: %.1f%% (%d/%d)" % (
                yr, ys.get("rate", 0), ys.get("success", 0), ys.get("total", 0)))

    # DB 统计
    db = report.get("database", {})
    if "error" not in db:
        lines.append("\n## 二、入库数据统计")
        lines.append("  报告总数:        %d" % db.get("total_reports", 0))
        lines.append("  入库字段总数:    %d" % db.get("total_fields", 0))
        lines.append("  有效字段:        %d" % db.get("valid_fields", 0))
        lines.append("  标记异常字段:    %d" % db.get("flagged_fields", 0))
        lines.append("  字段合格率:      %.1f%%" % db.get("field_pass_rate", 0))
        lines.append("  待复核标记:      %d" % db.get("pending_flags", 0))
        lines.append("  历史标记总数:    %d" % db.get("total_flags", 0))

        # 各表行数
        table_rows = db.get("table_rows", {})
        if table_rows:
            lines.append("\n  各表数据行数:")
            for tbl, cnt in sorted(table_rows.items()):
                cn = {"income_sheet": "利润表", "balance_sheet": "资产负债表",
                      "cash_flow_sheet": "现金流量表",
                      "core_performance_indicators_sheet": "核心指标"}.get(tbl, tbl)
                lines.append("    %-12s: %d 行" % (cn, cnt))

        # NULL 率
        null_rates = db.get("field_null_rates", {})
        if null_rates:
            lines.append("\n  关键字段缺失率:")
            for fld, stats in sorted(null_rates.items()):
                total = stats.get("total", 1)
                null_count = stats.get("null_count", 0)
                rate = round(null_count / max(total, 1) * 100, 1)
                flag = " ⚠" if rate > 20 else ""
                lines.append("    %s: %.1f%% (%d/%d)%s" % (
                    fld, rate, null_count, total, flag))
    else:
        lines.append("\n## 二、数据库不可用: %s" % db.get("error"))

    lines.append("\n" + "-" * 60)
    return "\n".join(lines)


def _chart_to_b64(fig) -> str:
    """matplotlib figure 转 base64"""
    import io, base64
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="white")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    buf.close()
    import matplotlib.pyplot as plt
    plt.close(fig)
    return b64


def _build_charts(report: dict) -> dict:
    """生成所有图表的 base64 字符串"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    # 中文字体
    for fn in ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]:
        try:
            fm.findfont(fn, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [fn]
            break
        except Exception:
            pass
    plt.rcParams["axes.unicode_minus"] = False

    charts = {}
    ext = report.get("extraction", {})

    # ---- 图1：按年份 PDF 成功率柱状图 ----
    by_year = ext.get("by_year", {})
    if by_year:
        fig, ax = plt.subplots(figsize=(8, 4))
        years = sorted(by_year.keys())
        rates = [by_year[y].get("rate", 0) for y in years]
        counts = [by_year[y].get("total", 0) for y in years]
        bars = ax.bar(years, rates, color=["#2563eb" if r >= 95 else "#d97706" if r >= 85 else "#dc2626" for r in rates])
        for bar, rate, cnt in zip(bars, rates, counts):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                    f"{rate}%\n({cnt})", ha="center", fontsize=9)
        ax.set_ylim(0, 110)
        ax.set_ylabel("成功率 (%)")
        ax.set_title("各年份 PDF 提取成功率")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        charts["yearly_rate"] = _chart_to_b64(fig)

    # ---- 图2：四表字段数对比 ----
    by_table = ext.get("by_table", {})
    if by_table:
        fig, ax = plt.subplots(figsize=(8, 4))
        cn_map = {"income_sheet": "利润表", "balance_sheet": "资产负债表",
                  "cash_flow_sheet": "现金流量表",
                  "core_performance_indicators_sheet": "核心指标"}
        labels = [cn_map.get(t, t) for t in sorted(by_table.keys())]
        founds = [by_table[t].get("found", 0) for t in sorted(by_table.keys())]
        totals = [by_table[t].get("total", 0) for t in sorted(by_table.keys())]
        fields = [by_table[t].get("total_fields", 0) for t in sorted(by_table.keys())]
        colors = ["#2563eb", "#059669", "#7c3aed", "#d97706"]
        bars = ax.bar(labels, fields, color=colors)
        for bar, f, found, tot in zip(bars, fields, founds, totals):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(fields)*0.01,
                    f"{f}字段\n{found}/{tot}份", ha="center", fontsize=9)
        ax.set_ylabel("字段总数")
        ax.set_title("各财务报表提取字段数")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        charts["table_fields"] = _chart_to_b64(fig)

    # ---- 图3：映射置信度分布 ----
    confidences = []
    for log in _cached_logs(ext):
        for tbl in log.get("tables", {}).values():
            for f in tbl.get("fields", []):
                c = f.get("confidence", 0)
                if c > 0:
                    confidences.append(c)

    if confidences:
        fig, ax = plt.subplots(figsize=(8, 4))
        accurate = sum(1 for c in confidences if c >= 80)
        fuzzy = sum(1 for c in confidences if 60 <= c < 80)
        dubious = sum(1 for c in confidences if c < 60)
        pie_labels = [f"准确(≥80): {accurate}", f"模糊(60-79): {fuzzy}", f"存疑(<60): {dubious}"]
        pie_vals = [accurate, fuzzy, dubious]
        pie_colors = ["#059669", "#d97706", "#dc2626"]
        ax.pie(pie_vals, labels=pie_labels, colors=pie_colors, autopct="%1.1f%%",
               startangle=90, textprops={"fontsize": 11})
        ax.set_title("字段映射置信度分布")
        charts["confidence_pie"] = _chart_to_b64(fig)

    return charts


_cached_log_list = None


def _cached_logs(ext=None):
    """缓存日志列表，避免重复加载"""
    global _cached_log_list
    if _cached_log_list is None:
        from pdf_extractor.extraction_logger import load_all_logs
        _cached_log_list = load_all_logs()
    return _cached_log_list


def format_html(report: dict) -> str:
    """格式化为 HTML（含图表）"""
    import matplotlib
    matplotlib.use("Agg")

    ext = report.get("extraction", {})
    db = report.get("database", {})
    charts = _build_charts(report)

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>数据质量报告</title>
<style>
*{box-sizing:border-box}body{font-family:'Microsoft YaHei',sans-serif;max-width:1100px;margin:20px auto;padding:0 20px;background:#f0f2f5}
.card{background:#fff;border-radius:10px;padding:24px;margin:20px 0;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
h1{color:#1a1a2e;font-size:24px}h2{color:#16213e;border-left:4px solid #2563eb;padding-left:12px;margin-top:0}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px}
.stat{text-align:center;padding:16px 12px;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0}
.stat .num{font-size:30px;font-weight:700}.stat .label{font-size:13px;color:#64748b;margin-top:4px}
.good{color:#059669}.warn{color:#d97706}.bad{color:#dc2626}
.chart-row{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.chart-row .chart-card{background:#fff;border-radius:10px;padding:16px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
.chart-row .chart-card.full{grid-column:1/-1}
.chart-row img{width:100%%;height:auto}
table{width:100%%;border-collapse:collapse;margin-top:12px}
th,td{padding:10px 14px;text-align:left;border-bottom:1px solid #e2e8f0}
th{background:#f8fafc;font-weight:600;font-size:13px;color:#475569}
td{font-size:14px}
.flag-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600}
.flag-ok{background:#dcfce7;color:#166534}
.flag-warn{background:#fef3c7;color:#92400e}
.flag-err{background:#fee2e2;color:#991b1b}
.refresh{color:#94a3b8;font-size:13px}
@media(max-width:768px){.chart-row{grid-template-columns:1fr}}
</style></head><body>
<h1>📊 数据质量报告</h1>
<p class="refresh">生成时间: %s | 每次刷新页面自动更新</p>
""" % report["generated_at"]

    # ---- 统计卡片 ----
    html += '<div class="card"><h2>一、PDF 提取概览</h2><div class="stat-grid">'
    pdf_total = ext.get("total_pdfs", 0)
    pdf_rate = ext.get("pdf_success_rate", 0)
    html += '<div class="stat"><div class="num">%d</div><div class="label">PDF 总数</div></div>' % pdf_total
    html += '<div class="stat"><div class="num %s">%.1f%%</div><div class="label">PDF 成功率</div></div>' % (
        "good" if pdf_rate > 90 else ("warn" if pdf_rate > 80 else "bad"), pdf_rate)
    html += '<div class="stat"><div class="num">%d</div><div class="label">字段总数</div></div>' % ext.get("total_fields", 0)
    html += '<div class="stat"><div class="num">%.1f</div><div class="label">平均字段/PDF</div></div>' % ext.get("avg_fields_per_pdf", 0)
    html += '<div class="stat"><div class="num %s">%.1f</div><div class="label">平均映射置信度</div></div>' % (
        "good" if ext.get("field_confidence_avg", 0) > 80 else "warn", ext.get("field_confidence_avg", 0))
    html += '<div class="stat"><div class="num">%d</div><div class="label">提取成功</div></div>' % ext.get("successful_pdfs", 0)
    html += '</div></div>'

    # ---- 图表区 ----
    has_charts = any(charts.values())
    if has_charts:
        html += '<div class="card"><h2>二、可视化图表</h2>'

        if "yearly_rate" in charts or "table_fields" in charts:
            html += '<div class="chart-row">'
            if "yearly_rate" in charts:
                html += '<div class="chart-card"><h3 style="font-size:15px;margin-top:0">📈 各年份成功率</h3><img src="data:image/png;base64,%s"></div>' % charts["yearly_rate"]
            if "table_fields" in charts:
                html += '<div class="chart-card"><h3 style="font-size:15px;margin-top:0">📊 各表字段数</h3><img src="data:image/png;base64,%s"></div>' % charts["table_fields"]
            html += '</div>'

        if "confidence_pie" in charts:
            html += '<div class="chart-row"><div class="chart-card full"><h3 style="font-size:15px;margin-top:0">🎯 字段映射置信度分布</h3><img src="data:image/png;base64,%s" style="max-width:500px;display:block;margin:0 auto"></div></div>' % charts["confidence_pie"]

        html += '</div>'

    # ---- 详细表格 ----
    by_table = ext.get("by_table", {})
    by_year = ext.get("by_year", {})
    if by_table or by_year:
        html += '<div class="card"><h2>三、详细数据</h2>'

        if by_table:
            html += '<h3 style="font-size:15px">各表提取统计</h3><table>'
            html += '<tr><th>报表</th><th>成功率</th><th>提取/总数</th><th>字段数</th><th>状态</th></tr>'
            cn_map = {"income_sheet": "利润表", "balance_sheet": "资产负债表",
                      "cash_flow_sheet": "现金流量表",
                      "core_performance_indicators_sheet": "核心指标"}
            for tbl, stats in sorted(by_table.items()):
                rate = stats.get("rate", 0)
                badge = "flag-ok" if rate > 95 else ("flag-warn" if rate > 80 else "flag-err")
                badge_text = "优秀" if rate > 95 else ("正常" if rate > 80 else "需关注")
                html += '<tr><td>%s</td><td>%.1f%%</td><td>%d/%d</td><td>%d</td><td><span class="flag-badge %s">%s</span></td></tr>' % (
                    cn_map.get(tbl, tbl), rate, stats.get("found", 0),
                    stats.get("total", 0), stats.get("total_fields", 0), badge, badge_text)
            html += '</table>'

        if by_year:
            html += '<h3 style="font-size:15px;margin-top:20px">按年份统计</h3><table>'
            html += '<tr><th>年份</th><th>成功率</th><th>成功/总数</th></tr>'
            for yr in sorted(by_year.keys()):
                ys = by_year[yr]
                html += '<tr><td>%s</td><td>%.1f%%</td><td>%d/%d</td></tr>' % (
                    yr, ys.get("rate", 0), ys.get("success", 0), ys.get("total", 0))
            html += '</table>'
        html += '</div>'

    # ---- DB 统计 ----
    if "error" not in db:
        html += '<div class="card"><h2>四、入库数据</h2><div class="stat-grid">'
        html += '<div class="stat"><div class="num">%d</div><div class="label">报告总数</div></div>' % db.get("total_reports", 0)
        html += '<div class="stat"><div class="num %s">%.1f%%</div><div class="label">字段合格率</div></div>' % (
            "good" if db.get("field_pass_rate", 0) > 95 else "warn", db.get("field_pass_rate", 0))
        html += '<div class="stat"><div class="num bad">%d</div><div class="label">标记异常</div></div>' % db.get("flagged_fields", 0)
        html += '<div class="stat"><div class="num">%d</div><div class="label">待复核</div></div>' % db.get("pending_flags", 0)
        table_rows = db.get("table_rows", {})
        if table_rows:
            html += '</div><h3 style="font-size:15px;margin-top:16px">各表入库行数</h3><table>'
            cn_map = {"income_sheet": "利润表", "balance_sheet": "资产负债表",
                      "cash_flow_sheet": "现金流量表",
                      "core_performance_indicators_sheet": "核心指标"}
            for tbl, cnt in sorted(table_rows.items()):
                html += '<tr><td>%s</td><td>%d 行</td></tr>' % (cn_map.get(tbl, tbl), cnt)
            html += '</table>'
        html += '</div>'
    else:
        html += '<div class="card"><h2>四、数据库状态</h2><p style="color:#94a3b8">数据库暂不可用（%s），入库统计需先运行 import_db.py</p></div>' % db.get("error")

    html += '<p style="text-align:center;color:#94a3b8;margin-top:30px">数据来源: data/logs/extraction/ | 每次刷新自动重新计算</p>'
    html += '</body></html>'
    return html


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    report = generate_report()
    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif "--html" in sys.argv:
        print(format_html(report))
    else:
        print(format_console(report))
