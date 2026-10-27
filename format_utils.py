# -*- coding: utf-8 -*-
"""
智能数值格式化工具模块
处理：万元↔亿元自动换算、百分比字段显示%、每股字段显示元
"""

# 百分比字段（数据库存的是比值，如 50.5 表示 50.5%）
PERCENTAGE_FIELDS = {
    "operating_revenue_yoy", "operating_revenue_qoq",
    "net_profit_yoy", "net_profit_qoq", "net_profit_ex_yoy",
    "roe", "roe_weighted",
    "gross_profit_margin", "net_profit_margin",
    "debt_ratio",
    "operating_cash_ratio", "investing_cash_ratio", "financing_cash_ratio",
    "net_cash_flow_yoy",
}

# 每股字段（数据库单位是元/股）
PER_SHARE_FIELDS = {
    "eps", "net_asset_per_share", "operating_cf_per_share",
}


def format_smart_number(num, field_name=None):
    if num is None:
        return "0"
    try:
        n = float(num)
    except (ValueError, TypeError):
        return str(num)

    if field_name and field_name.lower() in PERCENTAGE_FIELDS:
        return f"{n:.2f}%"

    if field_name and field_name.lower() in PER_SHARE_FIELDS:
        if n >= 10000:
            return f"{n:,.2f} 元/股"
        return f"{n:.2f} 元/股"

    abs_n = abs(n)
    if abs_n >= 10000:
        return f"{n / 10000:.2f} 亿元"
    elif abs_n >= 1:
        return f"{n:,.2f} 万元"
    elif abs_n >= 0.01:
        return f"{n:.2f} 万元"
    return f"{n:.4f}"


def format_num_with_unit(num, field_name=None):
    if num is None:
        return "0", ""
    try:
        n = float(num)
    except (ValueError, TypeError):
        return str(num), ""

    if field_name and field_name.lower() in PERCENTAGE_FIELDS:
        return f"{n:.2f}", "%"

    if field_name and field_name.lower() in PER_SHARE_FIELDS:
        return f"{n:.2f}", "元/股"

    abs_n = abs(n)
    if abs_n >= 10000:
        return f"{n / 10000:.2f}", "亿元"
    elif abs_n >= 1:
        return f"{n:,.2f}", "万元"
    elif abs_n >= 0.01:
        return f"{n:.2f}", "万元"
    return f"{n:.4f}", ""


def get_chart_ylabel(field_name, values=None):
    if field_name and field_name.lower() in PERCENTAGE_FIELDS:
        return "%"
    if field_name and field_name.lower() in PER_SHARE_FIELDS:
        return "元/股"
    if values:
        try:
            max_val = max(abs(float(v)) for v in values if v is not None)
            if max_val >= 10000:
                return "亿元"
        except:
            pass
    return "万元"