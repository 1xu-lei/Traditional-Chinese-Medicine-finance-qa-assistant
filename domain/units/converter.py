# -*- coding: utf-8 -*-
"""
系统化单位转换器
替代 data_tool._normalize_units() 的 abs(value) > 1e8 启发式判断

设计原则:
  1. 字段分类驱动（非启发式）: amount/ratio/growth/per_share/count
  2. 比率/增长率永不缩放 (0.35 → 35.00% 而非 35.00 亿元)
  3. 金额字段从"元"(DB原始值) 自动选 亿元/万元/元 显示
  4. 每股字段显示为 元/股
"""
from typing import List, Tuple, Optional
from agent.taxonomy import (
    INDICATORS, IndicatorMeta, FieldCategory,
    get_category, is_ratio_or_growth,
)


class UnitConverter:
    """系统化财务数据单位转换"""

    # ---- 核心转换 ----

    @classmethod
    def convert(cls, value, field_name: str,
                target_unit: str = None) -> Tuple[float, str]:
        """
        将一个数据库原始值转换为显示值

        参数:
          value: 数据库中的原始数值
          field_name: 英文字段名 (如 'total_operating_revenue')
          target_unit: 目标单位 (仅 amount 类有效: '亿元'/'万元'/'元')

        返回:
          (normalized_value, unit_label)
          例: (45.23, "亿元"), (0.35, "%), (15.8, "元/股")
        """
        meta: Optional[IndicatorMeta] = INDICATORS.get(field_name)

        try:
            v = float(value)
        except (TypeError, ValueError):
            return value, ""

        # ---- 比率字段: 保持原始小数，显示为 % ----
        if meta and meta.category == FieldCategory.RATIO:
            # 比率在 DB 中以小数存储 (0.15 = 15%)，返回原值
            # 显示时乘以 100 加 %（由调用方格式化）
            return round(v * 100, 2), "%"

        # ---- 增长率: 同比率 ----
        if meta and meta.category == FieldCategory.GROWTH:
            return round(v * 100, 2), "%"

        # ---- 每股字段 ----
        if meta and meta.category == FieldCategory.PER_SHARE:
            return round(v, 4), "元/股"

        # ---- 计数字段 ----
        if meta and meta.category == FieldCategory.COUNT:
            return round(v, 2), meta.display_unit if meta else ""

        # ---- 金额字段: DB 存万元，按万元换算 ----
        source_unit = meta.source_unit if meta else "万元"
        default_display = meta.display_unit if meta else "亿元"

        if target_unit is None:
            target_unit = default_display

        # DB 存储单位 = 万元
        value_in_wan = v

        # 按目标单位缩放（1亿元 = 1万万元）
        if target_unit == "亿元":
            return round(value_in_wan / 10000, 4), "亿元"
        elif target_unit == "万元":
            return round(value_in_wan, 2), "万元"
        else:  # 元
            return round(value_in_wan * 10000, 2), "元"

    @classmethod
    def auto_convert(cls, value, field_name: str) -> Tuple[float, str]:
        """
        自动选择最佳显示单位（仅对 amount 类有效）

        规则:
          >= 1 亿  → 亿元
          >= 1 万  → 万元
          < 1 万   → 元
        """
        meta = INDICATORS.get(field_name)

        # 非金额字段直接返回
        if meta and meta.category != FieldCategory.AMOUNT:
            return cls.convert(value, field_name)

        try:
            v = float(value)
        except (TypeError, ValueError):
            return value, ""

        abs_v = abs(v)

        if abs_v >= 10_000:       # ≥1亿元（万元口径）
            return cls.convert(value, field_name, "亿元")
        elif abs_v >= 1:           # ≥1万元
            return cls.convert(value, field_name, "万元")
        else:                      # <1万元 → 元
            return cls.convert(value, field_name, "元")

    # ---- 批量转换 ----

    @classmethod
    def convert_data_rows(cls, data: list, field_names: list = None) -> list:
        """
        批量转换数据行中的所有字段值

        参数:
          data: [{"stock_abbr":"白云山","net_profit":4523000000,...}, ...]
          field_names: 需要转换的字段列表（None = 自动检测所有非元数据字段）

        返回:
          每行增加 _display 字段:
          {"net_profit": 4523000000,
           "net_profit_display": {"value": 45.23, "unit": "亿元"}, ...}
        """
        if not data:
            return data

        SKIP = {"stock_abbr", "stock_code", "report_year", "report_period",
                "rank", "note", "_tbl_priority", "_rn", "_no_data"}

        if field_names is None:
            field_names = [k for k in data[0].keys() if k not in SKIP]

        result = []
        for row in data:
            new_row = dict(row)
            for fn in field_names:
                if fn not in row:
                    continue
                val = row.get(fn)
                if val is None:
                    continue
                try:
                    display_val, unit = cls.auto_convert(val, fn)
                    new_row[f"{fn}_display"] = {"value": display_val, "unit": unit}
                except (TypeError, ValueError):
                    pass
            result.append(new_row)

        return result

    # ---- 格式化 ----

    @classmethod
    def format_value(cls, value, field_name: str,
                      target_unit: str = None) -> str:
        """
        将一个字段值格式化为完整字符串
        例: format_value(4523000000, "net_profit") → "45.23亿元"
            format_value(0.1523, "gross_profit_margin") → "15.23%"
            format_value(3.56, "eps") → "3.56元/股"
        """
        display_val, unit = cls.convert(value, field_name, target_unit)

        try:
            v = float(display_val)
            if unit == "%":
                return f"{v:.2f}%"
            elif unit == "元/股":
                return f"{v:.2f}{unit}"
            elif unit in ("亿元", "万元"):
                if abs(v) >= 100:
                    return f"{v:.2f}{unit}"
                else:
                    return f"{v:.4f}{unit}"
            else:
                return f"{v:,.2f}{unit}"
        except (TypeError, ValueError):
            return f"{display_val}{unit}"

    # ---- 数字合理性校验 ----

    @classmethod
    def validate_sanity(cls, value, field_name: str) -> Tuple[bool, str]:
        """
        检查单个值是否在合理范围内

        返回:
          (is_ok, message)
        """
        meta = INDICATORS.get(field_name)
        if not meta:
            return True, ""

        try:
            v = float(value)
        except (TypeError, ValueError):
            return True, ""

        cat = meta.category

        # 比率: 通常在 [-1, 10] 之间（即 -100% ~ 1000%）
        if cat == FieldCategory.RATIO:
            if not (-10.0 <= v <= 10.0):
                return False, f"{meta.cn_label} 值 {v} 超出合理比率范围 [-1000%, 1000%]"

        # 增长率: 通常在 [-1, 100] 之间
        elif cat == FieldCategory.GROWTH:
            if not (-100.0 <= v <= 100.0):
                return False, f"{meta.cn_label} 增长率 {v} 超出合理范围"

        # 每股: 通常在 [-1000, 100000] 之间
        elif cat == FieldCategory.PER_SHARE:
            if not (-1000.0 <= v <= 100000.0):
                return False, f"{meta.cn_label} 每股值 {v} 超出合理范围"

        # 金额: 不设上限，不校验（因为不同公司规模差异大）
        return True, ""

    @classmethod
    def validate_data_rows(cls, data: list) -> List[Tuple[str, str, bool]]:
        """
        批量校验数据行，返回所有异常
        返回: [(字段名, 异常描述, 是否严重), ...]
        """
        if not data:
            return []

        SKIP = {"stock_abbr", "stock_code", "report_year", "report_period",
                "rank", "note", "_tbl_priority", "_rn", "_no_data"}
        field_names = [k for k in data[0].keys() if k not in SKIP]

        issues = []
        for row in data:
            for fn in field_names:
                val = row.get(fn)
                if val is None:
                    continue
                ok, msg = cls.validate_sanity(val, fn)
                if not ok:
                    company = row.get("stock_abbr", "?")
                    issues.append((fn, f"[{company}] {msg}", False))

        return issues
