# -*- coding: utf-8 -*-
"""数据处理工具：calc + compare合并"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "models", "model2_question_understanding"))
from agent.tools.base import BaseTool
from calc import calc_single, calc_batch, calc_yoy, calc_cagr
from compare import rank, filter_rows, group_stats, intersect_rank


class DataTool(BaseTool):
    name = "data_process"
    description = """对已查数据做二次计算或对比。calc:计算派生指标 / compare:排名/筛选/统计。
必须在 sql_query 之后调用。"""
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["calc", "compare"], "description": "操作类型"},
            "data": {"type": "array", "description": "sql_query 返回的数据数组"},
            "params": {"type": "object", "description": "计算或对比参数"}
        },
        "required": ["action", "data"]
    }
    priority = 65

    async def execute(self, action: str = "", data: list = None, params: dict = None) -> str:
        """异步执行"""
        import json as _json
        result = self.run({"action": action, "data": data or [], "params": params or {}})
        return _json.dumps(result, ensure_ascii=False, default=str)

    # 不参与单位换算的字段
    SKIP_FIELDS = {"stock_abbr", "stock_code", "report_year", "report_period", "rank", "note",
                   "eps", "roe", "roe_weighted_excl_non_recurring", "gross_profit_margin",
                   "net_profit_margin", "asset_liability_ratio"}

    def _normalize_units(self, data):
        """统一单位：确保所有金额字段都是万元，返回(归一化数据, 原始单位说明)"""
        if not data:
            return data, "无数据"

        # 检查第一个非空金额值来推断单位
        sample_val = None
        for row in data[:5]:
            for k, v in row.items():
                if k in self.SKIP_FIELDS or v is None:
                    continue
                try:
                    sample_val = float(v)
                    if sample_val != 0:
                        break
                except:
                    continue
            if sample_val and sample_val != 0:
                break

        if sample_val is None:
            return data, "无金额"

        # 判断单位：值>1e8认为是元，需要除以10000转万元
        if abs(sample_val) > 1e8:
            unit = "元"
            factor = 10000
        else:
            unit = "万元"
            factor = 1

        if factor == 1:
            return data, unit

        # 换算
        normalized = []
        for row in data:
            new_row = {}
            for k, v in row.items():
                if k in self.SKIP_FIELDS or v is None:
                    new_row[k] = v
                    continue
                try:
                    new_row[k] = round(float(v) / factor, 4)
                except:
                    new_row[k] = v
            normalized.append(new_row)
        return normalized, unit

    def run(self, inputs: dict) -> dict:
        action = inputs.get("action", "")
        data = inputs.get("data", [])
        params = inputs.get("params", {})

        if not data:
            return {"success": False, "error": "缺少data参数，需要先调用sql_query"}

        try:
            if action == "calc":
                return self._calc(data, params)
            elif action == "compare":
                return self._compare(data, params)
            else:
                return {"success": False, "error": f"未知action: {action}，支持calc/compare"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _calc(self, data, params):
        formula = params.get("formula", "")
        if not formula:
            return {"success": False, "error": "缺少formula参数"}

        results = calc_batch(formula, data)
        valid = [r for r in results if r.get("value") is not None]
        valid.sort(key=lambda x: x.get("value", 0), reverse=True)

        return {
            "success": bool(valid),
            "action": "calc",
            "formula": formula,
            "results": valid,
            "count": len(valid),
        }

    def _compare(self, data, params):
        # 统一单位
        data, unit = self._normalize_units(data)

        mode = params.get("mode", "rank")
        field = params.get("field", "")
        top_k = params.get("top_k")

        if mode == "rank":
            if not field:
                return {"success": False, "error": "rank模式需要field参数"}
            ranked = rank(data, field, top_k=top_k)
            return {"success": True, "action": "compare", "mode": "rank", "results": ranked, "unit": unit}

        elif mode == "filter":
            conditions = params.get("conditions", [])
            if not conditions:
                return {"success": False, "error": "filter模式需要conditions参数"}
            filtered = filter_rows(data, conditions)
            return {"success": True, "action": "compare", "mode": "filter", "results": filtered, "unit": unit}

        elif mode == "stats":
            if not field:
                return {"success": False, "error": "stats模式需要field参数"}
            stats = group_stats(data, field)
            return {"success": True, "action": "compare", "mode": "stats", "results": stats, "unit": unit}

        elif mode == "intersect":
            field1 = params.get("field1", "")
            field2 = params.get("field2", "")
            if not field1 or not field2:
                return {"success": False, "error": "intersect模式需要field1和field2参数"}
            ranked1 = rank(data, field1, top_k=top_k or 5)
            ranked2 = rank(data, field2, top_k=top_k or 5)
            intersection = intersect_rank(ranked1, ranked2, top_k=top_k or 5)
            return {"success": True, "action": "compare", "mode": "intersect", "results": intersection, "unit": unit}

        else:
            return {"success": False, "error": f"未知mode: {mode}，支持rank/filter/stats/intersect"}
