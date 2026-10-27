# -*- coding: utf-8 -*-
"""SQL查询工具：封装pipeline.query() + 趋势/对比专用查询"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "models", "model2_question_understanding"))
from agent.tools.base import BaseTool
from agent.config import get_database_config

PERIOD_PRIORITY = {"FY": 4, "Q3": 3, "H1": 2, "Q1": 1}
PERIOD_FALLBACK = ["FY", "Q3", "H1", "Q1"]


class SQLTool(BaseTool):
    name = "sql_query"
    description = """查询财务数据库。用于：查具体数值（营收、净利润、总资产等）"""
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "用户原始问题或查询描述"},
            "understanding": {"type": "object", "description": "可选：pipeline.understand() 的理解结果"},
            "query_type": {"type": "string", "enum": ["auto", "trend", "compare"], "description": "查询类型：auto自动判断"}
        },
        "required": ["question"]
    }
    priority = 100

    def __init__(self):
        self._pipeline = None
        self._db_conn = None
        self._column_cache = None  # 启动时懒加载，缓存所有表的列名

    def _get_pipeline(self):
        if self._pipeline is None:
            from pipeline import FinancialQAPipeline
            self._pipeline = FinancialQAPipeline()
        return self._pipeline

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

    # ========== 启动时列名缓存（一次性加载，不运行时查 information_schema）==========
    def refresh_column_cache(self):
        """强制刷新列名缓存（新建表后调用）"""
        self._column_cache = None
        self._ensure_column_cache()

    def _ensure_column_cache(self):
        if self._column_cache is not None:
            return
        cache = {}
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            schema = get_database_config()["database"]
            cursor.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = %s",
                (schema,),
            )
            for table_name, column_name in cursor.fetchall():
                if table_name not in cache:
                    cache[table_name] = set()
                cache[table_name].add(column_name)
            cursor.close()
            self._column_cache = cache
            print(f"[SQLTool] Column cache loaded: {sum(len(v) for v in cache.values())} columns across {len(cache)} tables")
        except Exception as e:
            print(f"[SQLTool] Column cache load failed: {e}")
            self._column_cache = {}  # 空缓存，后续检查都会返回不存在

    def _column_exists(self, table, column):
        """查缓存，不访问数据库"""
        self._ensure_column_cache()
        return column in self._column_cache.get(table, set())

    def _execute_sql(self, sql):
        """执行 SQL，返回结果列表或 None"""
        if not sql or sql.startswith("--"):
            return None
        try:
            conn = self._get_conn()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            results = cursor.fetchall()
            cursor.close()
            return results if results else None
        except Exception as e:
            print(f"  [SQLTool] SQL执行错误: {e}")
            return None

    def _list_available_tables(self, metric_en):
        """检查指标可能存在于哪些表（基于 field_matcher 的 table + 备选表）"""
        candidates = [metric_en]
        tables_checked = ["income_sheet", "balance_sheet", "cash_flow_sheet"]
        available = []
        for tbl in tables_checked:
            for col in candidates:
                if self._column_exists(tbl, col):
                    available.append(tbl)
                    break
            # 也检查列名本身
            if self._column_exists(tbl, metric_en):
                if tbl not in available:
                    available.append(tbl)
        return available

    # ========== 字段解析：统一调 field_matcher，不另起一套 ==========
    def _resolve_metric(self, metric_name):
        """
        通过 pipeline 的 field_matcher 解析字段名
        返回 (db_field, table) 或 (None, None)
        """
        if not metric_name:
            return None, None
        try:
            from field_matcher import match as field_match
            results = field_match(metric_name)
            if results and len(results) > 0:
                r = results[0]
                if r.get("score", 0) >= 0.8:
                    return r["column_en"], r["table_name"]
            return None, None
        except Exception as e:
            print(f"[SQLTool] field_matcher error: {e}")
            return None, None

    def _resolve_metric_with_fallback(self, metric_name):
        """
        Resolve field with explicit fallback. Handles both Chinese and English metric names.
        Returns (db_field, table, display_name, substituted)
        """
        # 1. Chinese name -> use field_matcher (single source of truth)
        is_chinese = any('\u4e00' <= c <= '\u9fff' for c in metric_name)
        if is_chinese:
            db_field, table = self._resolve_metric(metric_name)
        else:
            db_field, table = metric_name, None

        # 2. Check if column exists in DB
        if db_field:
            if table and self._column_exists(table, db_field):
                return db_field, table, metric_name, False
            for tbl in ["income_sheet", "balance_sheet", "cash_flow_sheet"]:
                if self._column_exists(tbl, db_field):
                    return db_field, tbl, metric_name, False

        # 3. Column not found -> try known fallbacks (explicitly tagged)
        fallback_map = {
            "total_profit": [("net_profit", "Net Profit")],
            "operating_profit": [("net_profit", "Net Profit")],
        }
        key = db_field or metric_name
        if key in fallback_map:
            for fb_field, fb_display in fallback_map[key]:
                for tbl in ["income_sheet"]:
                    if self._column_exists(tbl, fb_field):
                        return fb_field, tbl, fb_display, True

        return None, None, None, False

    async def execute(self, question: str = "", understanding: dict = None, query_type: str = "auto") -> str:
        """异步执行（ReAct 引擎调用）"""
        import json as _json
        result = self.run({"question": question, "understanding": understanding, "query_type": query_type})
        return _json.dumps(result, ensure_ascii=False, default=str)

    def run(self, inputs: dict) -> dict:
        question = inputs.get("question", "")
        understanding = inputs.get("understanding")
        query_type = inputs.get("query_type", "auto")

        if not question:
            return {"success": False, "error": "缺少question参数"}

        # ---- 自动补全 understanding ----
        # 当 LLM 直接传裸 question 时，内部跑 pipeline 提取指标
        # 这避免了 ReAct 循环中 sql_query → model2 → sql_query 的死循环
        try:
            if understanding is None or not understanding.get("indicators"):
                print(f"[SQLTool] understanding 缺失或空，自动补全...")
                try:
                    pipeline = self._get_pipeline()
                    understanding = pipeline.understand(question)
                    print(f"[SQLTool] pipeline 结果: intent={understanding.get('intent')}, "
                          f"indicators={understanding.get('indicators')}, "
                          f"companies={understanding.get('companies')}")
                except Exception as e:
                    print(f"[SQLTool] pipeline.understand() 失败: {e}，用关键词兜底")
                    understanding = self._keyword_understand(question)
        except Exception as e:
            print(f"[SQLTool] 自动补全失败: {e}")
            understanding = {"intent": "basic_query", "companies": [], "indicators": [], "years": [], "period": None}

        # 兜底：确保 understanding 不为 None
        if understanding is None:
            understanding = {"intent": "basic_query", "companies": [], "indicators": [], "years": [], "period": None}

        return self._dispatch_query(inputs, question, understanding, query_type)

    def _keyword_understand(self, question: str) -> dict:
        """关键词兜底提取：当 pipeline 模型不可用时，从问题文本中提取指标/公司/年份"""
        import re
        from field_dict import SCHEMA_DICT

        # 公司名关键词匹配
        COMPANY_NAMES = [
            "白云山", "云南白药", "华润三九", "同仁堂", "太极集团", "片仔癀",
            "金花股份", "葵花药业", "达仁堂", "天士力", "康恩贝", "东阿阿胶",
            "江中药业", "健民集团", "千金药业", "羚锐制药", "济川药业",
            "昆药集团", "康美药业", "康缘药业", "康弘药业", "振东制药",
            "桂林三金", "太龙药业", "以岭药业", "步长制药", "信邦制药",
            "红日药业", "仁和药业", "珍宝岛", "中恒集团", "马应龙",
            "亚宝药业", "九芝堂", "益佰制药", "众生药业", "广誉远",
            "贵州三力", "方盛制药", "奇正藏药", "佐力药业",
        ]
        companies = [c for c in COMPANY_NAMES if c in question]
        if not companies:
            # 模糊匹配
            for c in COMPANY_NAMES:
                if len(c) >= 2 and c[:2] in question:
                    companies.append(c)
                    break

        # 指标关键词匹配：从 question 中找 SCHEMA_DICT 的中文同义词
        # 长词覆盖短词（同 field_matcher.match_from_question），
        # 避免"净资产收益率"里的"净资产"误命中 equity_total_equity 等子串歧义
        syn_pairs = []
        for en_name, info in SCHEMA_DICT.items():
            for syn in info.get("synonyms", []):
                if len(syn) >= 2:
                    syn_pairs.append((syn, en_name))
        syn_pairs.sort(key=lambda x: -len(x[0]))  # 长词优先
        indicators = []
        matched_syns = []  # 已匹配的 (同义词, 字段)，用于长词覆盖短词
        for syn, en_name in syn_pairs:
            if syn in question:
                covered = False
                for other_syn, _ in matched_syns:
                    if syn != other_syn and syn in other_syn:
                        covered = True
                        break
                if not covered:
                    matched_syns.append((syn, en_name))
                    indicators.append(en_name)

        # 去重（保留第一个匹配到的）
        seen = set()
        unique_indicators = []
        for ind in indicators:
            if ind not in seen:
                seen.add(ind)
                unique_indicators.append(ind)

        # 如果没有精确匹配，尝试用常见指标关键词
        if not unique_indicators:
            KEYWORD_TO_FIELD = {
                "营收": "total_operating_revenue",
                "营业收入": "total_operating_revenue",
                "收入": "total_operating_revenue",
                "净利润": "net_profit",
                "净利": "net_profit",
                "利润": "total_profit",
                "总资产": "asset_total_assets",
                "资产": "asset_total_assets",
                "毛利率": "gross_profit_margin",
                "净利率": "net_profit_margin",
                "现金流": "operating_cf_net_amount",
                "资产负债率": "asset_liability_ratio",
                "负债": "liability_total_liabilities",
                "研发": "operating_expense_rnd_expenses",
            }
            for kw, field in KEYWORD_TO_FIELD.items():
                if kw in question and field not in seen:
                    seen.add(field)
                    unique_indicators.append(field)

        # 年份
        years = []
        yr_match = re.findall(r"(20\d{2})", question)
        if yr_match:
            years = [int(y) for y in yr_match]

        # 报告期
        period = None
        if "Q1" in question.upper() or "一季度" in question or "一季报" in question:
            period = "Q1"
        elif "Q2" in question.upper() or "二季度" in question or "中报" in question or "半年报" in question:
            period = "H1"
        elif "Q3" in question.upper() or "三季度" in question or "三季报" in question:
            period = "Q3"
        elif "Q4" in question.upper() or "四季度" in question or "年报" in question:
            period = "FY"

        # top_k
        top_k = None
        top_match = re.search(r"前\s*(\d+|[一二三四五六七八九十]+)", question)
        if top_match:
            cn_num = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                      "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
            raw = top_match.group(1)
            top_k = int(raw) if raw.isdigit() else cn_num.get(raw)

        # 意图判断
        intent = "basic_query"
        if top_k:
            intent = "stat_query"
        if any(kw in question for kw in ["对比", "比较", "VS", "vs", "和", "与"]):
            intent = "comparison"
        if any(kw in question for kw in ["趋势", "变化", "近几年", "近三年", "近五年"]):
            intent = "time_trend"

        result = {
            "intent": intent,
            "companies": companies,
            "indicators": unique_indicators,
            "years": years,
            "period": period,
            "top_k": top_k,
            "entities": {"COMP": companies, "METRIC": unique_indicators, "PERIOD": [period] if period else []},
            "needs_chart": False,
            "chart_type": None,
            "needs_rag": False,
        }
        print(f"[SQLTool] 关键词提取: intent={intent}, companies={companies}, "
              f"indicators={unique_indicators}, years={years}, period={period}, top_k={top_k}")
        return result

    def _dispatch_query(self, inputs, question, understanding, query_type):
        """查询调度：根据 query_type 分发到不同处理方法"""
        try:
            if query_type == "trend":
                return self._query_trend(inputs)
            elif query_type == "compare":
                return self._query_compare(inputs)
            else:
                return self._query_with_understanding(question, understanding)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _query_with_understanding(self, question, understanding):
        """直接用 understanding 生成 SQL 并执行，不再重新跑 NER/Model2/time_parser"""
        if not understanding:
            return {"success": False, "error": "缺少 understanding"}

        from sql_generator import SQLGenerator
        from schema_mapping import match as schema_match

        intent = understanding.get("intent", "basic_query")
        companies = understanding.get("companies", [])
        indicators = understanding.get("indicators", [])
        years = understanding.get("years", [])
        period = understanding.get("period")
        top_k = understanding.get("top_k")

        # 构建 understanding dict 给 sql_generator
        gen_und = {
            "intent": intent,
            "company": companies[0] if companies else None,
            "companies": companies,
            "year": years[0] if years else None,
            "period": period,
            "top_k": top_k,
            "indicators": indicators,
            "is_multi_indicator": len(indicators) > 1,
            "is_multi_company": len(companies) > 1,
        }

        # 字段匹配（indicators 可能是中文指标名或英文字段名）
        all_matches = []
        seen_cols = set()
        print(f"[SQLTool] 开始匹配字段，indicators={indicators}")
        for ind in indicators:
            # 英文字段名直接查 SCHEMA_DICT，中文走 schema_match
            if all(ord(c) < 128 for c in ind):
                # KG Resolver 已处理的英文字段名，直接查列是否存在
                from field_dict import SCHEMA_DICT
                if ind in SCHEMA_DICT:
                    info = SCHEMA_DICT[ind]
                    table = info["table"]
                    col_exists = self._column_exists(table, ind)
                    print(f"[SQLTool] 英文字段 {ind} -> 表 {table}, 列存在: {col_exists}")
                    if col_exists:
                        matches = [{"column_en": ind, "table_name": table,
                                    "unit": info["unit"], "score": 1.0,
                                    "display": table + "." + ind + ": " + info["label"]}]
                    else:
                        matches = None
                else:
                    print(f"[SQLTool] 英文字段 {ind} 不在 SCHEMA_DICT 中")
                    matches = None
            else:
                matches = schema_match(ind, top_k=3)
                print(f"[SQLTool] 中文字段 {ind} -> 匹配结果: {matches}")
            if matches:
                col_en = matches[0]["column_en"]
                if col_en not in seen_cols:
                    all_matches.append(matches[0])
                    seen_cols.add(col_en)

        print(f"[SQLTool] 最终匹配结果: {all_matches}")
        if not all_matches:
            return {"success": False, "error": "未匹配到字段", "data": []}

        # 生成 SQL
        sql_gen = SQLGenerator()
        sql = sql_gen.generate(gen_und, all_matches, question)
        if not sql or sql.startswith("--"):
            return {"success": False, "error": "SQL生成失败", "data": []}

        # 执行（带重试）
        result_data, sql_str, retry_count, reason = self._execute_with_retry(sql, gen_und, sql_gen, all_matches, question)

        # 对比/排名查询：每家公司只保留优先级最高的报告期
        if result_data and intent in ("comparison", "stat_query"):
            result_data = self._dedup_by_company(result_data)
            # 补充查了但没数据的公司（只有在用户明确指定了公司列表时才补充）
            # 对于排名查询（无明确公司列表），不要补充 NER 可能误识别的公司
            if companies and len(companies) > 0:
                # 判断是否是排名查询（无明确公司，只有 top_k）
                # 只有在用户明确指定了公司名时才补充，比如 "云南白药和片仔癀的营收对比"
                # 对于像 "营收排名前五" 这类查询，不补充
                is_ranking_query = (top_k is not None) or (not any(c in question for c in companies))
                if not is_ranking_query:
                    found = {r.get("stock_abbr") for r in result_data}
                    for comp in companies:
                        if comp not in found:
                            result_data.append({"stock_abbr": comp, "report_year": years[0] if years else "", "_no_data": True})

        # 格式化答案
        if result_data:
            answer = self._format_answer(intent, companies, indicators, years, period, top_k, result_data, all_matches)
        else:
            answer = ""

        # ---- 回写自动补全后的 understanding（B3 槽位回填）----
        # ReAct 引擎只传裸 question 时，本方法在 run() 内已完成
        # pipeline/keyword 自动补全并据此生成 SQL；把补全结果以顶层键
        # 回写进返回 JSON（只增键），供引擎侧解析 company/indicator/year
        # 槽位，避免真实链路指标槽位取不到。
        try:
            from agent.taxonomy import en_to_cn as _en_to_cn
        except Exception:
            _en_to_cn = lambda name: name
        # 以实际匹配/执行的字段为准（英文列名 → 中文标签），未匹配到时回退到
        # understanding 里已解析的指标词（中文词/概念组会原样保留）。
        resolved_labels = []
        for match in all_matches or []:
            col_en = match.get("column_en") if isinstance(match, dict) else ""
            if isinstance(col_en, str) and col_en.strip():
                label = _en_to_cn(col_en)
                if label and label not in resolved_labels:
                    resolved_labels.append(label)
        if not resolved_labels:
            for ind in understanding.get("indicators", []) or []:
                if ind and ind not in resolved_labels:
                    resolved_labels.append(ind)

        return {
            "success": bool(result_data),
            "data": result_data,
            "sql": sql_str,
            "answer": answer,
            "confidence": 0.85 if result_data else 0,
            "retry_count": retry_count,
            "reason": reason,
            "unit": "万元" if result_data else "",
            "companies": list(understanding.get("companies") or []),
            "indicators": resolved_labels,
            "years": list(understanding.get("years") or []),
        }

    def _execute_with_retry(self, sql, understanding, sql_gen, all_matches, question):
        """4级降级重试：完整 → 去period → 去year → 全库"""
        import re
        retries = [
            (False, "完整参数"),
            (True, "去掉period"),
        ]

        for i, (strip_period, desc) in enumerate(retries):
            current_sql = sql

            if strip_period:
                current_sql = re.sub(r"\s*AND\s+report_period\s*=\s*'[^']*'", "", current_sql)
                current_sql = re.sub(r"\s*report_period\s*=\s*'[^']*'\s*AND\s*", " ", current_sql)
                current_sql = re.sub(r"WHERE\s+AND", "WHERE", current_sql)

            print(f"  [重试{i}] {desc}")
            print(f"  SQL: {current_sql[:200]}...")
            result_data = self._execute_sql(current_sql)
            if result_data:
                print(f"  查询到 {len(result_data)} 条数据")
                return result_data, current_sql, i, f"第{i+1}次尝试成功（{desc}）"
            else:
                print(f"  查询无数据")

        # 最后尝试：去掉year，取最新（仅当用户未明确指定年份时）
        original_year = understanding.get("year")
        # 用户问题中明确提到了年份（如"2023年"）-> 不降级year
        has_explicit_year = bool(__import__("re").search(r"(^|\D)(20\d{2})(\D|$)", question))
        if not original_year and not has_explicit_year:
            u = dict(understanding)
            u["year"] = None
            u["period"] = None
            sql3 = sql_gen.generate(u, all_matches, question)
            if sql3 and not sql3.startswith("--"):
                sql3 = re.sub(r"\s*AND\s+report_period\s*=\s*'[^']*'", "", sql3)
                sql3 = re.sub(r"\s*report_period\s*=\s*'[^']*'\s*AND\s*", " ", sql3)
                sql3 = re.sub(r"WHERE\s+AND", "WHERE", sql3)
                print(f"  [重试2] 去掉year，取最新")
                result_data = self._execute_sql(sql3)
                if result_data:
                    return result_data, sql3, 2, "第3次尝试成功（去掉year）"

        return None, sql, len(retries), "所有重试均无数据"

    # 报告期优先级：FY > H1 > Q3 > Q1
    PERIOD_PRIORITY = {"FY": 4, "H1": 3, "Q3": 2, "Q1": 1}

    def _is_row_valid(self, row):
        """检查一行数据的比率字段是否在合理范围内（排除明显错误的提取结果）"""
        for key, val in row.items():
            if val is None:
                continue
            if key in ("stock_abbr", "report_year", "report_period", "stock_code", "rank", "note",
                       "_tbl_priority", "_rn", "_no_data"):
                continue
            try:
                v = float(val)
                # 比率字段：毛利率/净利率/ROE 合理范围 -100% ~ 100%
                if any(kw in key for kw in ("margin", "ratio", "roe", "growth")):
                    if v > 1000 or v < -1000:
                        return False
            except (ValueError, TypeError):
                pass
        return True

    def _count_valid_values(self, row):
        """统计行中非空且合理的值数量"""
        count = 0
        for key, val in row.items():
            if val is None:
                continue
            if key in ("stock_abbr", "report_year", "report_period", "stock_code", "rank", "note",
                       "_tbl_priority", "_rn", "_no_data"):
                continue
            try:
                float(val)
                count += 1
            except (ValueError, TypeError):
                pass
        return count

    def _dedup_by_company(self, data):
        """对比/排名查询：每家公司只保留优先级最高的有效报告期"""
        best = {}
        for row in data:
            name = row.get("stock_abbr", "")
            rp = row.get("report_period", "")
            pri = self.PERIOD_PRIORITY.get(rp, 0)
            row_valid = self._is_row_valid(row)

            if name not in best:
                best[name] = (row, pri, row_valid)
            else:
                old_row, old_pri, old_valid = best[name]
                # 优先选择有效数据
                if row_valid and not old_valid:
                    best[name] = (row, pri, row_valid)
                elif row_valid == old_valid:
                    if pri > old_pri:
                        best[name] = (row, pri, row_valid)
                    elif pri == old_pri:
                        old_nulls = self._count_valid_values(old_row)
                        new_nulls = self._count_valid_values(row)
                        if new_nulls > old_nulls:
                            best[name] = (row, pri, row_valid)
        return [v[0] for v in best.values()]

    def _format_answer(self, intent, companies, indicators, years, period, top_k, data, matches):
        """格式化答案文本"""
        if not data:
            return "未查询到相关数据。"

        # 指标中文名
        indicator_cn = indicators[0] if indicators else ""
        if matches and matches[0].get("display"):
            disp = matches[0]["display"]
            if ": " in disp:
                indicator_cn = disp.split(": ", 1)[1]
            elif "：" in disp:
                indicator_cn = disp.split("：", 1)[1]

        value_cols = [k for k in data[0].keys()
                      if k not in ("stock_abbr", "report_year", "report_period", "stock_code", "rank", "note",
                                   "_tbl_priority", "_rn")]

        def fmt_num(num):
            if num is None:
                return "0"
            try:
                n = float(num)
                if abs(n) >= 10000:
                    return "{:.2f}".format(n)
                elif abs(n) >= 1:
                    return "{:.2f}".format(n)
                else:
                    return "{:.4f}".format(n)
            except:
                return str(num)

        if intent == "basic_query":
            name = data[0].get("stock_abbr", companies[0] if companies else "")
            y = data[0].get("report_year", years[0] if years else "")
            if len(data) == 1:
                val = data[0].get(value_cols[0], 0) if value_cols else 0
                rp = data[0].get("report_period", "")
                pstr = "（" + rp + "）" if rp and rp != "FY" else ""
                return f"{name}{y}年{pstr}的{indicator_cn}是 {fmt_num(val)} 万元。"
            else:
                lines = [f"{name}{y}年{indicator_cn}："]
                for row in data:
                    val = row.get(value_cols[0], 0) if value_cols else 0
                    rp = row.get("report_period", "")
                    note = row.get("note", "")
                    line = f"  {rp if rp else '未知'}：{fmt_num(val)} 万元"
                    if note:
                        line += f"（{note}）"
                    lines.append(line)
                return "\n".join(lines)

        # time_trend placeholder
        pass
# stat_query / comparison
        # 从 matches 获取指标中文名
        metric_names = []
        for m in (matches or []):
            disp = m.get("display", "")
            cn = disp.split(": ", 1)[1] if ": " in disp else (disp.split("：", 1)[1] if "：" in disp else m.get("column_en", ""))
            metric_names.append(cn)
        if not metric_names:
            # 原 self.METRIC_EN_TO_CN 定义在已删除的 agent/agent.py，现按收敛
            # 方向改走 agent.taxonomy 单一英文列名->中文标签映射（未匹配原样保留）
            try:
                from agent.taxonomy import en_to_cn as _metric_en_to_cn
            except Exception:
                _metric_en_to_cn = lambda vc: vc
            metric_names = [_metric_en_to_cn(vc) for vc in value_cols]

        lines = []
        if top_k:
            title = "、".join(metric_names) if len(metric_names) > 1 else indicator_cn
            lines.append(f"{title}排名前{top_k}的企业：")
        else:
            title = "、".join(metric_names) if len(metric_names) > 1 else indicator_cn
            lines.append(f"{title}查询结果：")

        for i, row in enumerate(data[:10], 1):
            name = row.get("stock_abbr", "未知")
            y = row.get("report_year", "")
            rp = row.get("report_period", "")
            note = row.get("note", "")
            pstr = f" {rp}" if rp and rp != "FY" else ""
            nstr = f"（{note}）" if note else ""

            # 查了但没数据的公司
            if row.get("_no_data"):
                if y:
                    lines.append(f"  {i}. {name}（{y}年{pstr}）：暂无数据")
                else:
                    lines.append(f"  {i}. {name}：暂无数据")
                continue

            if len(value_cols) <= 1:
                # 单指标
                val = row.get(value_cols[0], 0) if value_cols else 0
                unit = "万元" if "margin" not in (value_cols[0] if value_cols else "") and "ratio" not in (value_cols[0] if value_cols else "") and "roe" not in (value_cols[0] if value_cols else "") else "%"
                if y:
                    lines.append(f"  {i}. {name}（{y}年{pstr}）：{fmt_num(val)} {unit}{nstr}")
                else:
                    lines.append(f"  {i}. {name}：{fmt_num(val)} {unit}{nstr}")
            else:
                # 多指标：每个指标一行
                if y:
                    lines.append(f"  {i}. {name}（{y}年{pstr}）：")
                else:
                    lines.append(f"  {i}. {name}：")
                for vc, mn in zip(value_cols, metric_names):
                    val = row.get(vc)
                    if val is not None:
                        unit = "%" if any(kw in vc for kw in ("margin", "ratio", "roe", "growth")) else "万元"
                        lines.append(f"     {mn}：{fmt_num(val)} {unit}")
                if note:
                    lines.append(f"     备注：{note}")
        return "\n".join(lines)

    def _query_trend(self, inputs):
        """
        趋势查询：每年只返回一条，优先线 FY > Q3 > H1 > Q1
        字段解析统一走 field_matcher，回退时显式标记
        """
        raw_metric = inputs.get("metric", "net_profit")
        company = inputs.get("company", "")
        year_start = inputs.get("year_start", 2022)
        year_end = inputs.get("year_end", 2026)

        if not company:
            return {"success": False, "error": "缺少company参数"}

        # 优先用 field_matcher 解析字段名
        # 如果 raw_metric 已经是英文名（如 "net_profit"），直接尝试
        actual_metric, table, display_name, substituted = self._resolve_metric_with_fallback(raw_metric)

        if not actual_metric or not table:
            # field_matcher 也没解析到，用原始值尝试所有表兜底
            actual_metric = raw_metric
            for tbl in ["income_sheet", "balance_sheet", "cash_flow_sheet"]:
                if self._column_exists(tbl, actual_metric):
                    table = tbl
                    break

        if not table or not self._column_exists(table, actual_metric):
            # 在所有表中都找不到该列
            return {
                "success": False,
                "data": [],
                "error": f"数据库中不存在字段「{raw_metric}」，且无合适替代字段",
                "reason": "field_not_found",
                "metric": raw_metric,
            }

        conn = self._get_conn()
        c = conn.cursor(dictionary=True)

        all_rows = []
        try:
            sql = "SELECT stock_abbr, report_year, report_period, %s FROM %s WHERE stock_abbr LIKE %%s AND report_year BETWEEN %%s AND %%s AND %s IS NOT NULL" % (actual_metric, table, actual_metric)
            c.execute(sql, ("%" + company + "%", year_start, year_end))
            all_rows.extend(c.fetchall())
        except Exception as e:
            c.close()
            return {"success": False, "data": [], "error": str(e), "reason": "query_error", "metric": actual_metric}

        c.close()

        if not all_rows:
            # 尝试用 pipeline 兜底
            try:
                pipeline = self._get_pipeline()
                result = pipeline.query(inputs.get("question", ""))
                if result.get("success") and result.get("data"):
                    return {
                        "success": True,
                        "data": result["data"],
                        "query_type": "trend",
                        "company": company,
                        "metric": actual_metric,
                        "note": "使用pipeline兜底查询",
                        "substituted": substituted,
                        "original_metric": raw_metric if substituted else None,
                        "display_metric": display_name if substituted else None,
                    }
            except:
                pass
            return {"success": False, "data": [], "error": f"未找到 {company} 在 {year_start}-{year_end} 年的数据", "reason": "no_data"}

        # 每年只保留优先级最高的报告期
        best_per_year = {}
        for row in all_rows:
            year = row["report_year"]
            period = row.get("report_period", "")
            priority = PERIOD_PRIORITY.get(period, 0)
            if year not in best_per_year or priority > PERIOD_PRIORITY.get(best_per_year[year].get("report_period", ""), 0):
                best_per_year[year] = row

        result = sorted(best_per_year.values(), key=lambda x: x["report_year"])

        for row in result:
            if row.get("report_period") != "FY":
                row["note"] = "注：%d年无年报，使用%s数据" % (row["report_year"], row.get("report_period", ""))

        return {
            "success": bool(result),
            "data": result,
            "query_type": "trend",
            "company": company,
            "metric": actual_metric,
            "substituted": substituted,
            "original_metric": raw_metric if substituted else None,
            "display_metric": display_name if substituted else None,
        }

    def _query_compare(self, inputs):
        """对比查询：每家公司独立查，各自降级"""
        raw_metric = inputs.get("metric", "total_operating_revenue")
        companies = inputs.get("companies", [])
        year = inputs.get("year", 2024)

        if not companies:
            return {"success": False, "error": "缺少companies参数"}

        # 解析字段
        actual_metric, table, display_name, substituted = self._resolve_metric_with_fallback(raw_metric)
        if not actual_metric or not table:
            actual_metric = raw_metric
            for tbl in ["income_sheet", "balance_sheet", "cash_flow_sheet"]:
                if self._column_exists(tbl, actual_metric):
                    table = tbl
                    break

        if not table:
            return {"success": False, "data": [], "error": f"数据库中不存在字段「{raw_metric}」", "reason": "field_not_found"}

        conn = self._get_conn()
        c = conn.cursor(dictionary=True)

        results = []
        for company in companies:
            row = None
            used_period = None

            for period in PERIOD_FALLBACK:
                sql = "SELECT stock_abbr, report_year, report_period, %s FROM %s WHERE stock_abbr LIKE %%s AND report_year = %%s AND report_period = %%s AND %s IS NOT NULL LIMIT 1" % (actual_metric, table, actual_metric)
                c.execute(sql, ("%" + company + "%", year, period))
                row = c.fetchone()
                if row:
                    used_period = period
                    break

            if row:
                if used_period != "FY":
                    row["note"] = "注：%d年无年报，使用%s数据" % (year, used_period)
                results.append(row)
            else:
                results.append({
                    "stock_abbr": company,
                    "report_year": year,
                    actual_metric: None,
                    "note": "注：%d年无数据" % year,
                })

        c.close()

        return {
            "success": bool(results),
            "data": results,
            "query_type": "compare",
            "companies": companies,
            "metric": actual_metric,
            "substituted": substituted,
            "original_metric": raw_metric if substituted else None,
            "display_metric": display_name if substituted else None,
        }
