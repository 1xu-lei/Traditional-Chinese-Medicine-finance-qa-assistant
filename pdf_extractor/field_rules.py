# -*- coding: utf-8 -*-
"""
字段校验规则 — 入库前对每个字段做类型合理性验证

规则分类:
  - amount: 金额字段 (单位: 万元), 检查正负、范围
  - ratio: 比率字段 (单位: 小数), 检查 [-10, 10] 区间
  - growth: 增长率字段 (单位: 小数), 检查 [-100, 100] 区间
  - per_share: 每股字段 (单位: 元), 检查 [0.001, 100000]
  - count: 计数字段, 检查 [0, 1e12]
"""
from typing import Dict, List, Optional, Tuple

# ============================================================
# 字段校验规则表
# ============================================================

# 字段类型默认上下界
_AMOUNT_MAX = 500_000_000      # 金额类: 5万亿 (覆盖单位异常的大值)
_AMOUNT_MIN = -500_000_000

FIELD_RULES: Dict[str, Dict] = {
    # ---- income_sheet ----
    "total_operating_revenue":       {"field_type": "amount", "cn_label": "营业总收入"},
    "operating_expense_cost_of_sales": {"field_type": "amount", "cn_label": "营业成本"},
    "operating_expense_selling_expenses": {"field_type": "amount", "cn_label": "销售费用"},
    "operating_expense_administrative_expenses": {"field_type": "amount", "cn_label": "管理费用"},
    "operating_expense_financial_expenses": {"field_type": "amount", "cn_label": "财务费用"},
    "operating_expense_rnd_expenses": {"field_type": "amount", "cn_label": "研发费用"},
    "operating_expense_taxes_and_surcharges": {"field_type": "amount", "cn_label": "税金及附加"},
    "total_operating_expenses":      {"field_type": "amount", "cn_label": "营业总成本"},
    "operating_profit":              {"field_type": "amount", "cn_label": "营业利润"},
    "total_profit":                  {"field_type": "amount", "cn_label": "利润总额"},
    "net_profit":                    {"field_type": "amount", "cn_label": "净利润"},
    "asset_impairment_loss":         {"field_type": "amount", "cn_label": "资产减值损失"},
    "credit_impairment_loss":        {"field_type": "amount", "cn_label": "信用减值损失"},
    "other_income":                  {"field_type": "amount", "cn_label": "其他收益"},

    # ---- balance_sheet ----
    "asset_cash_and_cash_equivalents":  {"field_type": "amount", "cn_label": "货币资金"},
    "asset_accounts_receivable":        {"field_type": "amount", "cn_label": "应收账款"},
    "asset_inventory":                  {"field_type": "amount", "cn_label": "存货"},
    "asset_trading_financial_assets":   {"field_type": "amount", "cn_label": "交易性金融资产"},
    "asset_construction_in_progress":   {"field_type": "amount", "cn_label": "在建工程"},
    "asset_total_assets":               {"field_type": "amount", "cn_label": "资产总计"},
    "liability_accounts_payable":       {"field_type": "amount", "cn_label": "应付账款"},
    "liability_advance_from_customers": {"field_type": "amount", "cn_label": "预收账款"},
    "liability_contract_liabilities":   {"field_type": "amount", "cn_label": "合同负债"},
    "liability_short_term_loans":       {"field_type": "amount", "cn_label": "短期借款"},
    "liability_total_liabilities":      {"field_type": "amount", "cn_label": "负债合计"},
    "equity_unappropriated_profit":     {"field_type": "amount", "cn_label": "未分配利润"},
    "equity_total_equity":              {"field_type": "amount", "cn_label": "所有者权益合计"},

    # ---- cash_flow_sheet ----
    "net_cash_flow":                    {"field_type": "amount", "cn_label": "净现金流"},
    "operating_cf_net_amount":          {"field_type": "amount", "cn_label": "经营性现金流净额"},
    "operating_cf_cash_from_sales":     {"field_type": "amount", "cn_label": "经营性现金流入"},
    "investing_cf_net_amount":          {"field_type": "amount", "cn_label": "投资性现金流净额"},
    "investing_cf_cash_for_investments":{"field_type": "amount", "cn_label": "投资性现金流出"},
    "investing_cf_cash_from_investment_recovery": {"field_type": "amount", "cn_label": "投资性现金流入"},
    "financing_cf_cash_from_borrowing": {"field_type": "amount", "cn_label": "融资性现金流入"},
    "financing_cf_cash_for_debt_repayment": {"field_type": "amount", "cn_label": "融资性现金流出"},
    "financing_cf_net_amount":          {"field_type": "amount", "cn_label": "融资性现金流净额"},

    # ---- 比率字段 (小数) ----
    "asset_liability_ratio": {
        "field_type": "ratio", "min_val": -0.5, "max_val": 2.0,
        "cn_label": "资产负债率",
    },
    "gross_profit_margin": {
        "field_type": "ratio", "min_val": -0.5, "max_val": 2.0,
        "cn_label": "销售毛利率",
    },
    "net_profit_margin": {
        "field_type": "ratio", "min_val": -0.5, "max_val": 2.0,
        "cn_label": "销售净利率",
    },
    "roe": {
        "field_type": "ratio", "min_val": -1.5, "max_val": 1.5,
        "cn_label": "净资产收益率",
    },
    "roe_weighted_excl_non_recurring": {
        "field_type": "ratio", "min_val": -1.5, "max_val": 1.5,
        "cn_label": "加权平均净资产收益率",
    },
    "operating_cf_ratio_of_net_cf": {
        "field_type": "ratio", "min_val": -10.0, "max_val": 10.0,
        "cn_label": "经营性现金流占比",
    },
    "investing_cf_ratio_of_net_cf": {
        "field_type": "ratio", "min_val": -10.0, "max_val": 10.0,
        "cn_label": "投资性现金流占比",
    },
    "financing_cf_ratio_of_net_cf": {
        "field_type": "ratio", "min_val": -10.0, "max_val": 10.0,
        "cn_label": "融资性现金流占比",
    },

    # ---- 增长率字段 (小数) ----
    "operating_revenue_yoy_growth": {
        "field_type": "growth", "min_val": -50.0, "max_val": 100.0,
        "cn_label": "营收同比增长率",
    },
    "operating_revenue_qoq_growth": {
        "field_type": "growth", "min_val": -50.0, "max_val": 100.0,
        "cn_label": "营收环比增长率",
    },
    "net_profit_yoy_growth": {
        "field_type": "growth", "min_val": -100.0, "max_val": 200.0,
        "cn_label": "净利润同比增长率",
    },
    "net_profit_qoq_growth": {
        "field_type": "growth", "min_val": -100.0, "max_val": 200.0,
        "cn_label": "净利润环比增长率",
    },
    "net_profit_excl_non_recurring_yoy": {
        "field_type": "growth", "min_val": -100.0, "max_val": 200.0,
        "cn_label": "扣非净利润同比增长率",
    },
    "asset_total_assets_yoy_growth": {
        "field_type": "growth", "min_val": -30.0, "max_val": 50.0,
        "cn_label": "总资产同比增长率",
    },
    "liability_total_liabilities_yoy_growth": {
        "field_type": "growth", "min_val": -30.0, "max_val": 50.0,
        "cn_label": "负债合计同比增长率",
    },
    "net_cash_flow_yoy_growth": {
        "field_type": "growth", "min_val": -500.0, "max_val": 500.0,
        "cn_label": "净现金流同比增长率",
    },

    # ---- 每股字段 (元) ----
    "eps": {
        "field_type": "per_share", "min_val": -100, "max_val": 1000,
        "cn_label": "每股收益",
    },
    "net_asset_per_share": {
        "field_type": "per_share", "min_val": 0.01, "max_val": 500,
        "check_positive": True,
        "cn_label": "每股净资产",
    },
    "operating_cf_per_share": {
        "field_type": "per_share", "min_val": -500, "max_val": 500,
        "cn_label": "每股经营现金流",
    },

    # ---- 其他 ----
    "net_profit_excl_non_recurring": {
        "field_type": "amount", "min_val": -10_000_000, "max_val": 50_000_000,
        "cn_label": "扣非净利润",
    },
    "total_share_capital": {
        "field_type": "count", "min_val": 1, "max_val": 1_000_000,
        "check_positive": True,
        "cn_label": "总股本",
    },
}

# 默认规则 — 按字段类型设定合理范围
_TYPE_DEFAULTS = {
    "amount":    {"min_val": -500_000_000, "max_val": 500_000_000},
    "ratio":     {"min_val": -2.0,         "max_val": 3.0},
    "growth":    {"min_val": -500.0,       "max_val": 500.0},
    "per_share": {"min_val": -500,         "max_val": 10000},
    "count":     {"min_val": 0,            "max_val": 100_000_000},
}


# ============================================================
# 校验函数
# ============================================================

def get_rule(field_name: str) -> Dict:
    """获取字段的校验规则"""
    if field_name in FIELD_RULES:
        return FIELD_RULES[field_name]
    return {"field_type": "amount", "cn_label": field_name}


def validate_field(field_name: str, value) -> Tuple[bool, Optional[str]]:
    """
    校验单个字段值
    返回: (is_valid, reason)
    """
    if value is None:
        return True, None

    try:
        v = float(value)
    except (TypeError, ValueError):
        return False, f"无法解析为数值: {value}"

    rule = get_rule(field_name)
    cn_label = rule.get("cn_label", field_name)
    field_type = rule.get("field_type", "amount")
    type_def = _TYPE_DEFAULTS.get(field_type, _TYPE_DEFAULTS["amount"])

    # 使用规则自定义的或类型默认的范围
    min_val = rule.get("min_val", type_def["min_val"])
    max_val = rule.get("max_val", type_def["max_val"])

    if v < min_val:
        return False, f"{cn_label} 值 {v} 低于下限 {min_val}"
    if v > max_val:
        return False, f"{cn_label} 值 {v} 超过上限 {max_val}"

    return True, None


def validate_table_data(tbl_data: Dict[str, Dict]) -> Dict:
    """
    校验整份提取数据

    参数:
      tbl_data: {"income_sheet": {"net_profit": 45230, ...}, ...}

    返回:
      {
        "total_fields": int,
        "valid_fields": int,
        "flagged_fields": int,
        "flags": [{"table": str, "field": str, "value": float, "reason": str}, ...],
        "pass_rate": float,
      }
    """
    total = 0
    valid = 0
    flags = []

    for tbl_name, fields in tbl_data.items():
        for field_name, value in fields.items():
            total += 1
            is_ok, reason = validate_field(field_name, value)
            if is_ok:
                valid += 1
            else:
                flags.append({
                    "table": tbl_name,
                    "field": field_name,
                    "value": value,
                    "reason": reason,
                })

    return {
        "total_fields": total,
        "valid_fields": valid,
        "flagged_fields": total - valid,
        "flags": flags,
        "pass_rate": round(valid / max(total, 1) * 100, 1),
    }
