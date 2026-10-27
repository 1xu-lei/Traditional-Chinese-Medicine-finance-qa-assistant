# -*- coding: utf-8 -*-
"""
财务字段分类补充层（字段知识单一真相源在 field_dict.py，本文件不是真相源）

真相源：models/model2_question_understanding/field_dict.py
  - SCHEMA_DICT      : en 字段 -> {table, unit, label, synonyms}
  - CONCEPT_GROUPS   : 概念组 -> 基础字段成员

本模块不再复制任何字段的 label/synonyms/table，全部从 field_dict 导入组装；
只保留 field_dict 无法表达、历史由本模块承担的两类"分类补充"：
  1) FieldCategory 枚举与单位推导：field_dict 的 unit 只有 wan/ratio/yuan_per_share，
     这里按字段语义细分 ratio/growth/per_share/count，并补充存储/展示单位；
     与 unit 口径不一致的字段走 _CATEGORY_OVERRIDES 显式覆盖（如总股本）。
  2) 概念组补充：基础成员继承 field_dict.CONCEPT_GROUPS，本地只做增量
     （别名/描述/额外成员与纯补充组），不整表复制，行为与历史版本一致。

对外 API（INDICATORS / CONCEPT_GROUPS / IndicatorMeta / FieldCategory /
find_indicator / en_to_cn 等）与历史形态保持兼容：session.py 中文指标映射、
domain/units/converter.py 单位换算、app.py en->cn 上下文等调用方无需改动。
"""
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

# 复用 models/model2_question_understanding 下的真相源（与 kg_resolver 同导入方式）
_MODEL_DICT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "models", "model2_question_understanding",
)
if _MODEL_DICT_DIR not in sys.path:
    sys.path.insert(0, _MODEL_DICT_DIR)

from field_dict import SCHEMA_DICT, CONCEPT_GROUPS as _BASE_CONCEPT_GROUPS


# ============================================================
# 字段分类枚举（本地补充：field_dict 没有的 5 类展示口径）
# ============================================================

class FieldCategory:
    """财务字段的分类（用于单位换算和校验）"""
    AMOUNT = "amount"       # 金额字段 → 自动选 亿元/万元/元
    RATIO = "ratio"         # 比率字段 → 永不缩放，显示为 %
    GROWTH = "growth"       # 增长率字段 → 永不缩放，显示为 %
    PER_SHARE = "per_share" # 每股字段 → 显示为 元/股
    COUNT = "count"         # 计数字段 → 无单位


# ============================================================
# 指标元数据
# ============================================================

@dataclass
class IndicatorMeta:
    """单个财务指标的完整元数据（label/synonyms/table 取自 field_dict）"""
    en_name: str                # 英文字段名 (DB column)
    cn_label: str               # 中文标签（来自 SCHEMA_DICT.label）
    table: str                  # 所属表（来自 SCHEMA_DICT.table）
    category: str               # FieldCategory 分类（本地补充）
    synonyms: List[str] = field(default_factory=list)  # 同义词（来自 SCHEMA_DICT.synonyms）
    source_unit: str = "万元"   # 数据库存储单位（DB 存万元）
    display_unit: str = "亿元"  # 默认显示单位（仅 amount 类使用）
    concept_group: Optional[str] = None  # 所属概念组


# ============================================================
# 指标注册表组装：基础数据取自 SCHEMA_DICT，分类/单位为本地补充
# ============================================================

_GROWTH_RE = re.compile(r"(_yoy|_qoq|_growth)")

# 字段语义与 field_dict.unit 口径不一致时的显式分类覆盖（唯一例外表）
_CATEGORY_OVERRIDES = {
    "total_share_capital": FieldCategory.COUNT,  # 总股本=股数，不是金额
}

_SOURCE_DISPLAY_UNIT = {
    FieldCategory.AMOUNT: ("万元", "亿元"),
    FieldCategory.RATIO: ("1", "%"),
    FieldCategory.GROWTH: ("1", "%"),
    FieldCategory.PER_SHARE: ("元", "元/股"),
    FieldCategory.COUNT: ("股", "股"),
}


def _derive_category(unit: str, en_name: str) -> str:
    """按 field_dict.unit + 字段名推导 5 类分类（比率/增长率按命名细分）。"""
    if unit == "yuan_per_share":
        return FieldCategory.PER_SHARE
    if unit == "ratio":
        if _GROWTH_RE.search(en_name):
            return FieldCategory.GROWTH
        return FieldCategory.RATIO
    return FieldCategory.AMOUNT


# ============================================================
# 概念组补充（基础成员继承 field_dict.CONCEPT_GROUPS，只增不覆写）
# ============================================================
_GROUP_SUPPLEMENTS: Dict[str, Dict] = {
    "盈利能力": {
        "extra": ["net_profit", "operating_profit"],
        "aliases": ["盈利", "利润能力", "收益能力", "赚钱能力"],
        "description": "衡量企业获取利润的能力",
    },
    "偿债能力": {
        "extra": ["equity_total_equity"],
        "aliases": ["负债能力", "还债能力", "偿债", "债务水平"],
        "description": "衡量企业偿还债务的能力",
    },
    "增长能力": {
        "extra": [],
        "aliases": ["成长能力", "增速", "增长率", "发展趋势"],
        "description": "衡量企业的成长速度和发展趋势",
    },
    "运营能力": {
        "extra": [],
        "aliases": ["经营能力", "运转能力"],
        "description": "衡量企业日常运营的效率",
    },
    "现金流能力": {
        "extra": [],
        "aliases": ["现金能力", "现金流", "流动性"],
        "description": "衡量企业现金流的健康程度",
    },
    "资产规模": {
        "extra": [],
        "aliases": ["规模", "资产", "体量"],
        "description": "衡量企业的资产规模",
    },
    # ---- field_dict 没有的纯补充组（与 kg_resolver HIERARCHY 同级）----
    "营业成本构成": {
        "members": ["operating_expense_cost_of_sales", "operating_expense_selling_expenses",
                     "operating_expense_administrative_expenses", "operating_expense_financial_expenses",
                     "operating_expense_rnd_expenses"],
        "aliases": ["成本构成", "费用结构"],
        "description": "营业成本的各项构成",
    },
    "现金流构成": {
        "members": ["operating_cf_net_amount", "investing_cf_net_amount",
                     "financing_cf_net_amount"],
        "aliases": ["现金流结构"],
        "description": "现金流的经营/投资/融资三大构成",
    },
}


def _build_concept_groups() -> Dict[str, Dict]:
    """基础成员从 field_dict.CONCEPT_GROUPS 导入，再叠加本地补充。"""
    groups: Dict[str, Dict] = {}
    for name, base_members in _BASE_CONCEPT_GROUPS.items():
        sup = _GROUP_SUPPLEMENTS.get(name, {})
        members = list(base_members)
        for extra_en in sup.get("extra") or []:
            if extra_en not in members:
                members.append(extra_en)
        groups[name] = {
            "indicators": members,
            "aliases": list(sup.get("aliases") or []),
            "description": sup.get("description", ""),
        }
    for name, sup in _GROUP_SUPPLEMENTS.items():
        if name in groups:
            continue
        groups[name] = {
            "indicators": list(sup.get("members") or []),
            "aliases": list(sup.get("aliases") or []),
            "description": sup.get("description", ""),
        }
    return groups


CONCEPT_GROUPS: Dict[str, Dict] = _build_concept_groups()

# 字段 -> 首个所属概念组（用于 INDICATORS 的 concept_group 标注）
_CONCEPT_GROUP_OF: Dict[str, str] = {}
for _group_name, _group_info in CONCEPT_GROUPS.items():
    for _field_en in _group_info["indicators"]:
        _CONCEPT_GROUP_OF.setdefault(_field_en, _group_name)

# 兼容历史 INDICATORS.concept_group 标注：以下字段历史上归入某组但不在组
# 成员列表中（组列表与标注两套口径的历史遗留），显式覆盖保持行为不回退。
_CONCEPT_GROUP_OVERRIDES = {
    "roe": "盈利能力",
    "liability_total_liabilities_yoy_growth": "增长能力",
    "net_cash_flow_yoy_growth": "增长能力",
    "net_profit_excl_non_recurring_yoy": "增长能力",
}


def _build_indicators() -> Dict[str, IndicatorMeta]:
    """按 field_dict.SCHEMA_DICT 组装 57 个 IndicatorMeta（无本地字段复制）。"""
    metas: Dict[str, IndicatorMeta] = {}
    for en_name, info in SCHEMA_DICT.items():
        unit = info.get("unit", "") or ""
        category = _CATEGORY_OVERRIDES.get(en_name) or _derive_category(unit, en_name)
        source_unit, display_unit = _SOURCE_DISPLAY_UNIT[category]
        meta = IndicatorMeta(
            en_name=en_name,
            cn_label=info["label"],
            table=info["table"],
            category=category,
            synonyms=list(info.get("synonyms") or []),
            source_unit=source_unit,
            display_unit=display_unit,
        )
        if category in (FieldCategory.GROWTH, FieldCategory.RATIO):
            meta.concept_group = _CONCEPT_GROUP_OVERRIDES.get(
                en_name, _CONCEPT_GROUP_OF.get(en_name))
        metas[en_name] = meta
    return metas


INDICATORS: Dict[str, IndicatorMeta] = _build_indicators()


# ============================================================
# 便捷查询函数
# ============================================================

def get_indicator(en_name: str) -> Optional[IndicatorMeta]:
    """按英文字段名获取指标元数据"""
    return INDICATORS.get(en_name)


def get_indicator_by_cn(cn_name: str) -> Optional[IndicatorMeta]:
    """按中文标签名获取指标元数据"""
    for meta in INDICATORS.values():
        if meta.cn_label == cn_name:
            return meta
    return None


def get_indicator_by_synonym(synonym: str) -> Optional[IndicatorMeta]:
    """按同义词查找指标（精确匹配）"""
    for meta in INDICATORS.values():
        if synonym in meta.synonyms:
            return meta
    return None


def find_indicator(name: str) -> Optional[IndicatorMeta]:
    """
    智能查找指标：先精确匹配英文名 → 中文标签 → 同义词
    返回匹配到的 IndicatorMeta 或 None
    """
    # 1. 英文名精确匹配
    if name in INDICATORS:
        return INDICATORS[name]
    # 2. 中文标签精确匹配
    result = get_indicator_by_cn(name)
    if result:
        return result
    # 3. 同义词匹配
    return get_indicator_by_synonym(name)


def en_to_cn(en_name: str) -> str:
    """英文字段名 → 中文标签"""
    meta = INDICATORS.get(en_name)
    return meta.cn_label if meta else en_name


def cn_to_en(cn_name: str) -> str:
    """中文字段名 → 英文字段名"""
    meta = get_indicator_by_cn(cn_name)
    if meta:
        return meta.en_name
    meta = get_indicator_by_synonym(cn_name)
    return meta.en_name if meta else cn_name


def get_category(en_name: str) -> str:
    """获取字段分类 (amount/ratio/growth/per_share/count)"""
    meta = INDICATORS.get(en_name)
    return meta.category if meta else FieldCategory.AMOUNT


def is_ratio_or_growth(en_name: str) -> bool:
    """判断是否为比率或增长率字段（不参与单位换算）"""
    cat = get_category(en_name)
    return cat in (FieldCategory.RATIO, FieldCategory.GROWTH)


def expand_concept_group(group_name: str) -> List[str]:
    """
    展开概念组：输入"盈利能力" → 输出其下所有英文字段名列表
    支持别名匹配
    """
    # 先精确匹配
    for name, group in CONCEPT_GROUPS.items():
        if group_name == name or group_name in group.get("aliases", []):
            return group["indicators"]
    return []


def get_table_for_field(en_name: str) -> Optional[str]:
    """获取字段所在的数据库表"""
    meta = INDICATORS.get(en_name)
    return meta.table if meta else None


def build_cn_en_map_for_llm() -> Dict[str, str]:
    """构建 {中文标签: 英文名} 映射（供 LLM 上下文使用）"""
    return {meta.cn_label: meta.en_name for meta in INDICATORS.values()}


def build_en_cn_map() -> Dict[str, str]:
    """构建 {英文名: 中文标签} 映射"""
    return {meta.en_name: meta.cn_label for meta in INDICATORS.values()}


def get_skip_fields() -> Set[str]:
    """获取不参与单位换算的字段集合（替代 data_tool.SKIP_FIELDS）"""
    return {
        meta.en_name for meta in INDICATORS.values()
        if meta.category in (FieldCategory.RATIO, FieldCategory.GROWTH, FieldCategory.PER_SHARE, FieldCategory.COUNT)
    }
