# -*- coding: utf-8 -*-
"""财务分析报告工具：六段式标准报告"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.tools.base import BaseTool
from agent.config import get_database_config


class ReportTool(BaseTool):
    name = "report"
    description = """生成六段式财务分析报告(Markdown)：公司概况、盈利能力、偿债能力、现金流、风险预警、综合结论。"""
    parameters = {
        "type": "object",
        "properties": {
            "company": {"type": "string", "description": "公司名称"},
            "year": {"type": "string", "description": "年份（可选，默认最新）"}
        },
        "required": ["company"]
    }
    priority = 40

    def __init__(self):
        self._db_conn = None
        self._risk_tool = None

    def _get_conn(self):
        if self._db_conn is None:
            import mysql.connector
            db_cfg = get_database_config()
            self._db_conn = mysql.connector.connect(
                host=db_cfg["host"], port=db_cfg["port"],
                database=db_cfg["database"], user=db_cfg["user"],
                password=db_cfg["password"],
            )
        return self._db_conn

    async def execute(self, company: str = "", year: str = "") -> str:
        """异步执行"""
        import json as _json
        result = self.run({"company": company, "year": year} if year else {"company": company})
        return _json.dumps(result, ensure_ascii=False, default=str)

    def _get_risk_tool(self):
        if self._risk_tool is None:
            from agent.tools.risk_tool import RiskTool
            self._risk_tool = RiskTool()
        return self._risk_tool

    def run(self, inputs: dict) -> dict:
        company = inputs.get("company", "")
        year = inputs.get("year")

        if not company:
            return {"success": False, "error": "缺少company参数"}

        try:
            conn = self._get_conn()
            c = conn.cursor(dictionary=True)

            # 1. 查询基础数据
            data = self._query_report_data(c, company, year)
            if not data:
                return {"success": False, "error": "未找到 %s 的财务数据" % company}

            # 2. 取最新一期数据
            latest = data[0]

            # 3. 计算关键指标
            metrics = self._compute_metrics(latest)

            # 4. 风险预警
            risk_tool = self._get_risk_tool()
            risk_result = risk_tool.run({"company": company})
            alerts = risk_result.get("alerts", [])

            # 5. 生成报告
            report = self._build_report(company, latest, metrics, alerts)

            c.close()

            return {
                "success": True,
                "company": company,
                "report": report,
                "metrics": metrics,
                "alerts": alerts,
                "data": [latest],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _query_report_data(self, cursor, company, year=None):
        """查询报告所需数据"""
        queries = [
            ("income_sheet", "stock_abbr, report_year, report_period, total_operating_revenue, net_profit, operating_expense_cost_of_sales, total_operating_expenses"),
            ("balance_sheet", "stock_abbr, report_year, report_period, asset_total_assets, liability_total_liabilities, equity_total_equity, asset_accounts_receivable, asset_inventory, liability_short_term_loans, asset_cash_and_cash_equivalents"),
            ("cash_flow_sheet", "stock_abbr, report_year, report_period, operating_cf_net_amount, investing_cf_net_amount, financing_cf_net_amount"),
        ]

        all_rows = []
        for tbl, cols in queries:
            if year:
                sql = "SELECT %s FROM %s WHERE stock_abbr LIKE %%s AND report_year = %%s ORDER BY report_year DESC, report_period DESC LIMIT 10" % (cols, tbl)
                cursor.execute(sql, ("%" + company + "%", year))
            else:
                sql = "SELECT %s FROM %s WHERE stock_abbr LIKE %%s ORDER BY report_year DESC, report_period DESC LIMIT 10" % (cols, tbl)
                cursor.execute(sql, ("%" + company + "%",))
            all_rows.extend(cursor.fetchall())

        # 合并同公司同年同报告期
        merged = {}
        for row in all_rows:
            key = (row.get("stock_abbr", ""), row.get("report_year"), row.get("report_period"))
            if key not in merged:
                merged[key] = {"stock_abbr": key[0], "report_year": key[1], "report_period": key[2]}
            for k, v in row.items():
                if v is not None and k not in merged[key]:
                    merged[key][k] = v

        result = sorted(merged.values(), key=lambda x: (x.get("report_year", 0), x.get("report_period", "")), reverse=True)
        return result

    def _compute_metrics(self, row):
        """计算关键指标"""
        metrics = {}

        # 基础数据
        metrics["营业收入"] = self._f(row.get("total_operating_revenue"))
        metrics["净利润"] = self._f(row.get("net_profit"))
        metrics["总资产"] = self._f(row.get("asset_total_assets"))
        metrics["总负债"] = self._f(row.get("liability_total_liabilities"))

        # 盈利能力 - 需要计算
        revenue = metrics["营业收入"]
        cost = self._f(row.get("operating_expense_cost_of_sales"))
        np_val = metrics["净利润"]
        equity = self._f(row.get("equity_total_equity"))

        # 毛利率 = (营业收入 - 营业成本) / 营收
        if revenue and cost and revenue > 0:
            metrics["毛利率"] = (revenue - cost) / revenue
        else:
            metrics["毛利率"] = None

        # 净利率 = 净利润 / 营收
        if np_val and revenue and revenue > 0:
            metrics["净利率"] = np_val / revenue
        else:
            metrics["净利率"] = None

        # ROE = 净利润 / 净资产
        if np_val and equity and equity > 0:
            metrics["ROE"] = np_val / equity
        else:
            metrics["ROE"] = None

        # EPS 暂时无法计算（需要总股本数据）
        metrics["EPS"] = None

        # 偿债能力
        a = metrics["总资产"]
        l = metrics["总负债"]
        if a and l and a > 0:
            metrics["资产负债率"] = l / a
        else:
            metrics["资产负债率"] = None

        # 现金流
        metrics["经营现金流"] = self._f(row.get("operating_cf_net_amount"))

        return metrics

    def _evaluate_profitability(self, metrics):
        """评价盈利能力"""
        roe = metrics.get("ROE")
        npm = metrics.get("净利率")

        if roe and roe > 0.15:
            return "盈利能力较强"
        elif roe and roe > 0.05:
            return "盈利能力一般"
        elif roe and roe > 0:
            return "盈利能力偏弱"
        else:
            return "盈利能力较弱，需关注"

    def _evaluate_debt(self, metrics):
        """评价偿债能力"""
        dr = metrics.get("资产负债率")
        if dr is None:
            return "数据不足，无法评价"
        if dr < 0.4:
            return "偿债压力较低"
        elif dr < 0.6:
            return "偿债压力适中"
        else:
            return "偿债风险较高"

    def _evaluate_cashflow(self, metrics):
        """评价现金流"""
        ocf = metrics.get("经营现金流")
        if ocf is None:
            return "数据不足，无法评价"
        if ocf > 0:
            return "现金流健康"
        else:
            return "现金流承压"

    def _build_report(self, company, row, metrics, alerts):
        """构建六段式报告"""
        year = row.get("report_year", "")
        period = row.get("report_period", "")

        report = []
        report.append("# %s %s年%s 财务分析报告\n" % (company, year, period))

        # 第一部分：基础信息
        report.append("## 一、基础信息\n")
        report.append("| 项目 | 数值 |")
        report.append("|------|------|")
        report.append("| 公司名称 | %s |" % company)
        report.append("| 分析期间 | %s年%s |" % (year, period))
        report.append("| 营业收入 | %s |" % self._fmt(metrics.get("营业收入")))
        report.append("| 净利润 | %s |" % self._fmt(metrics.get("净利润")))
        report.append("")

        # 第二部分：盈利能力
        report.append("## 二、盈利能力\n")
        report.append("| 指标 | 数值 |")
        report.append("|------|------|")
        report.append("| 净利润 | %s |" % self._fmt(metrics.get("净利润")))
        report.append("| 毛利率 | %s |" % self._pct(metrics.get("毛利率")))
        report.append("| 净利率 | %s |" % self._pct(metrics.get("净利率")))
        report.append("| ROE | %s |" % self._pct(metrics.get("ROE")))
        report.append("| EPS | %s 元 |" % self._num(metrics.get("EPS")))
        report.append("")
        report.append("**评价：%s**\n" % self._evaluate_profitability(metrics))

        # 第三部分：偿债能力
        report.append("## 三、偿债能力\n")
        report.append("| 指标 | 数值 |")
        report.append("|------|------|")
        report.append("| 总资产 | %s |" % self._fmt(metrics.get("总资产")))
        report.append("| 总负债 | %s |" % self._fmt(metrics.get("总负债")))
        report.append("| 资产负债率 | %s |" % self._pct(metrics.get("资产负债率")))
        report.append("")
        report.append("**评价：%s**\n" % self._evaluate_debt(metrics))

        # 第四部分：现金流分析
        report.append("## 四、现金流分析\n")
        report.append("| 指标 | 数值 |")
        report.append("|------|------|")
        report.append("| 经营现金流净额 | %s |" % self._fmt(metrics.get("经营现金流")))
        report.append("")
        report.append("**评价：%s**\n" % self._evaluate_cashflow(metrics))

        # 第五部分：风险预警
        report.append("## 五、风险预警\n")
        if alerts:
            report.append("发现 %d 项风险：\n" % len(alerts))
            high = [a for a in alerts if a["severity"] == "高"]
            mid = [a for a in alerts if a["severity"] == "中"]
            low = [a for a in alerts if a["severity"] == "低"]
            if high:
                report.append("**【高风险】**")
                for a in high:
                    report.append("- %s：%s" % (a["rule"], a["detail"]))
            if mid:
                report.append("")
                report.append("**【中风险】**")
                for a in mid:
                    report.append("- %s：%s" % (a["rule"], a["detail"]))
            if low:
                report.append("")
                report.append("**【低风险】**")
                for a in low:
                    report.append("- %s：%s" % (a["rule"], a["detail"]))
        else:
            report.append("未发现明显风险。\n")

        # 第六部分：综合结论
        report.append("\n## 六、综合结论\n")
        conclusion = self._generate_conclusion(company, metrics, alerts)
        report.append(conclusion)

        return "\n".join(report)

    def _generate_conclusion(self, company, metrics, alerts):
        """生成综合结论"""
        parts = ["综合来看，%s" % company]

        # 盈利能力
        roe = metrics.get("ROE")
        if roe and roe > 0.15:
            parts.append("盈利能力较强，ROE为%s" % self._pct(roe))
        elif roe and roe > 0.05:
            parts.append("盈利能力一般，ROE为%s" % self._pct(roe))
        else:
            parts.append("盈利能力偏弱")

        # 偿债能力
        dr = metrics.get("资产负债率")
        if dr:
            if dr < 0.4:
                parts.append("偿债压力较低")
            elif dr < 0.6:
                parts.append("偿债压力适中")
            else:
                parts.append("偿债风险较高")

        # 现金流
        ocf = metrics.get("经营现金流")
        if ocf:
            if ocf > 0:
                parts.append("现金流状况健康")
            else:
                parts.append("现金流状况较弱")

        # 风险
        high = [a for a in alerts if a["severity"] == "高"]
        if high:
            parts.append("存在%d项高风险，建议重点关注%s" % (len(high), high[0]["suggestion"]))
        elif alerts:
            parts.append("存在%d项风险提示" % len(alerts))
        else:
            parts.append("未发现明显风险")

        return "，".join(parts) + "。"

    # ==================== 格式化工具 ====================

    @staticmethod
    def _f(val):
        if val is None: return None
        try: return float(val)
        except: return None

    @staticmethod
    def _fmt(val):
        if val is None: return "-"
        v = float(val)
        if abs(v) >= 10000:
            return "%.2f亿元" % (v / 10000)
        return "%.2f万元" % v

    @staticmethod
    def _pct(val):
        if val is None: return "-"
        return "%.2f%%" % (float(val) * 100)

    @staticmethod
    def _num(val):
        if val is None: return "-"
        return "%.4f" % float(val)
