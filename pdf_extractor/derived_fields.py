# -*- coding: utf-8 -*-
"""
派生字段计算模块（返工单 6 — 事一：公式双轨制）

规则:
  - 直取优先: DB已有且通过校验 → 保留
  - 公式兜底: 直取缺失 → 同报告依赖字段齐全 → 计算 → 合理性校验 → 入库
  - 恒等式校验: equity_total_equity 直取值必须过 |E−(A−L)| <= max(|E|,1)*1%
    不过则用 A−L 覆盖

每条公式标注《企业会计准则》出处，计算结果同时记录 source='calculated' + formula_key。
"""


def _safe_div(a, b):
    """安全除法，b 为 0 或 None 时返回 None"""
    if a is None or b is None or b == 0:
        return None
    return a / b


# ============================================================
# 派生字段定义
# ============================================================

DERIVED_FIELDS = {
    # ── 现金流量表 ──
    "net_cash_flow": {
        "table": "cash_flow_sheet",
        "formula": lambda op, iv, fn: op + iv + fn,
        "deps": [
            ("cash_flow_sheet", "operating_cf_net_amount"),
            ("cash_flow_sheet", "investing_cf_net_amount"),
            ("cash_flow_sheet", "financing_cf_net_amount"),
        ],
        "rationale": "《企业会计准则第31号——现金流量表》：现金及现金等价物净增加额=三类活动净额之和(±汇率变动)",
        "range": None,
        "field_type": "amount",
    },

    # ── 核心指标表 ──
    "gross_profit_margin": {
        "table": "core_performance_indicators_sheet",
        "formula": lambda rev, cost: _safe_div(rev - cost, rev),
        "deps": [
            ("income_sheet", "total_operating_revenue"),
            ("income_sheet", "operating_expense_cost_of_sales"),
        ],
        "rationale": "《企业会计准则第30号——财务报表列报》：毛利率=(营业收入-营业成本)/营业收入",
        "range": (-1.0, 2.0),
        "field_type": "ratio",
    },

    "net_profit_margin": {
        "table": "core_performance_indicators_sheet",
        "formula": lambda np, rev: _safe_div(np, rev),
        "deps": [
            ("income_sheet", "net_profit"),
            ("income_sheet", "total_operating_revenue"),
        ],
        "rationale": "《企业会计准则第30号》：销售净利率=净利润/营业收入",
        "range": (-2.0, 3.0),
        "field_type": "ratio",
    },

    "asset_liability_ratio": {
        "table": "balance_sheet",
        "formula": lambda l, a: _safe_div(l, a),
        "deps": [
            ("balance_sheet", "liability_total_liabilities"),
            ("balance_sheet", "asset_total_assets"),
        ],
        "rationale": "《企业会计准则——基本准则》：资产负债率=负债合计/资产总计",
        "range": (0.0, 2.0),
        "field_type": "ratio",
    },

    # ── 资产负债表（恒等式校验 + 兜底计算）──
    "equity_total_equity": {
        "table": "balance_sheet",
        "formula": lambda a, l: a - l,
        "deps": [
            ("balance_sheet", "asset_total_assets"),
            ("balance_sheet", "liability_total_liabilities"),
        ],
        "rationale": "《企业会计准则——基本准则》第26条：资产=负债+所有者权益",
        "range": None,
        "field_type": "amount",
        # 特殊: 直取值必须过恒等式校验，不过则覆盖
        "identity_check": True,
    },
}


def compute_derived(db_data, field_key):
    """
    对单份报告的单个派生字段执行计算。

    参数:
      db_data: dict {(table_name, field_name): value}  同公司同年同期所有表的数据
      field_key: DERIVED_FIELDS 的 key

    返回: (computed_value, source_info) 或 (None, None)
    """
    spec = DERIVED_FIELDS[field_key]
    args = []
    for tbl, fld in spec["deps"]:
        val = db_data.get((tbl, fld))
        if val is None:
            return None, None
        args.append(float(val))  # 统一转 float，避免 Decimal × float 冲突

    result = spec["formula"](*args)
    if result is None:
        return None, None

    # 合理性校验
    rng = spec.get("range")
    if rng is not None:
        lo, hi = rng
        if result < lo or result > hi:
            return None, None

    source_info = {
        "source": "calculated",
        "formula_key": field_key,
        "rationale": spec["rationale"],
        "input_values": {dep[1]: args[i] for i, dep in enumerate(spec["deps"])},
    }
    return round(result, 6), source_info


def check_identity(asset, liability, equity_direct):
    """
    恒等式校验: |E − (A − L)| <= max(|E|, 1) * 1%

    返回: (passed: bool, computed_equity: float, detail: str)
    """
    asset = float(asset) if asset is not None else None
    liability = float(liability) if liability is not None else None
    equity_direct = float(equity_direct) if equity_direct is not None else None

    if asset is None or liability is None:
        return True, None, "依赖字段缺失，无法校验"
    computed = asset - liability
    if equity_direct is None:
        return False, computed, "直取缺失，使用恒等式值"

    tolerance = max(abs(equity_direct), 1.0) * 0.01
    diff = abs(equity_direct - computed)
    if diff <= tolerance:
        return True, equity_direct, "通过恒等式校验"
    else:
        return False, computed, (
            f"恒等式校验失败: E={equity_direct:.2f} A-L={computed:.2f} "
            f"diff={diff:.2f} > tol={tolerance:.2f}"
        )
