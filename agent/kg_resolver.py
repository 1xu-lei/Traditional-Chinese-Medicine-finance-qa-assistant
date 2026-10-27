# -*- coding: utf-8 -*-
"""
轻量级财务知识图谱 Resolver (P1)

功能：
  P1.1 同义词归一：税后利润 → 净利润（250+ 条同义词）
  P1.2 层级展开：盈利能力 → [毛利率, 净利率, ROE, 加权ROE]（8 个核心概念组）
  P1.3 公式推导：暂缓（DB 已有计算字段）

插入位置：Agent.query() 中 understand() 之后、_plan_for_intent() 之前
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "models", "model2_question_understanding"))
from field_dict import SCHEMA_DICT, CONCEPT_GROUPS
from field_matcher import match as field_match


# ============================================================
# P1.1 同义词映射（用户口语 → 标准中文字段名）
# ============================================================

SYNONYMS = {
    'PB': '市净率',
    'PE': '市盈率',
    'ROA': '总资产收益率',
    'eps': '每股收益',
    'roa': '总资产收益率',
    'roe': '加权平均净资产收益率',
    '主营收入': '营业总收入',
    '净利增长': '净利润同比增长率',
    '净利增长率': '净利润同比增长率',
    '净资产合计': '所有者权益合计',
    '利润': '利润总额',
    '利润增长': '净利润同比增长率',
    '利润增长率': '净利润同比增长率',
    '同比增长': '营收同比增长率',
    '同比增长率': '营收同比增长率',
    '存货净额': '存货',
    '市净率': '市净率',
    '市盈率': '市盈率',
    '广告费': '销售费用',
    '应收余额': '应收账款',
    '应收款项': '应收账款',
    '应收金额': '应收账款',
    '开发费用': '研发费用',
    '归母收益': '归母净利润',
    '总营收': '营业总收入',
    '总资': '资产总计',
    '总资产收益率': '总资产收益率',
    '总销售': '营业总收入',
    '所有者权益': '所有者权益合计',
    '投资活动现金流量': '投资性现金流净额',
    '投资现金流量': '投资性现金流净额',
    '收入增长率': '营收同比增长率',
    '权益': '所有者权益合计',
    '权益收益率': '加权平均净资产收益率',
    '毛利': '销售毛利率',
    '毛利润率': '销售毛利率',
    '现金流': '经营性现金流净额',
    '盈利': '归母净利润',
    '短期债务': '短期借款',
    '短期融资': '短期借款',
    '短期负债': '短期借款',
    '研发费': '研发费用',
    '研究开发费用': '研发费用',
    '研究费用': '研发费用',
    '科研费用': '研发费用',
    '税前收益': '利润总额',
    '筹资活动现金流': '筹资性现金流净额',
    '管理费': '管理费用',
    '经营收益': '营业利润',
    '经营活动现金流量': '经营性现金流净额',
    '股东权益合计': '所有者权益合计',
    '股东权益回报率': '加权平均净资产收益率',
    '股息率': '股息率',
    '营业收益': '营业利润',
    '营业费用': '销售费用',
    '营收增长': '营收同比增长率',
    '营收增长率': '营收同比增长率',
    '融资活动现金流': '筹资性现金流净额',
    '负债': '负债合计',
    '负债总量': '负债合计',
    '负债规模': '负债合计',
    '负债资产比': '资产负债率',
    '财务费': '财务费用',
    '资产': '资产总计',
    '资产合计': '资产总计',
    '资产回报率': '总资产收益率',
    '资产总值': '资产总计',
    '资产总量': '资产总计',
    '资产收益率': '总资产收益率',
    '资金': '货币资金',
    '赚了多少钱': '归母净利润',
    '运营成本': '营业成本',
    '银行存款': '货币资金',
    '销售净利润率': '销售净利率',
    '销售毛利': '销售毛利率',
    '销售毛利润率': '销售毛利率',
    '销售费': '销售费用',
}


# ============================================================
# P1.2 层级展开（概念组 → 子指标列表）
# ============================================================

# 复用 field_dict 的 CONCEPT_GROUPS，再加上自定义层级
HIERARCHY = dict(CONCEPT_GROUPS)

# 补充 field_dict 里没有的层级
HIERARCHY.update({
    # ==================== 盈利能力（核心概念）====================
    "盈利能力": [
        "gross_profit_margin",           # 销售毛利率
        "net_profit_margin",             # 销售净利率
        "roe_weighted_excl_non_recurring",  # 加权平均净资产收益率
    ],

    # ==================== 偿债能力（核心概念）====================
    "偿债能力": [
        "asset_liability_ratio",         # 资产负债率
        "liability_total_liabilities",   # 负债合计
        "equity_total_equity",           # 所有者权益合计
    ],

    # ==================== 运营能力（核心概念）====================
    "运营能力": [
        "total_operating_revenue",       # 营业总收入
        "total_operating_expenses",      # 营业总成本
        "operating_profit",              # 营业利润
    ],

    # ==================== 成长能力（核心概念）====================
    "成长能力": [
        "operating_revenue_yoy_growth",  # 营收同比增长率
        "net_profit_yoy_growth",         # 净利润同比增长率
        "operating_revenue_qoq_growth",  # 营收环比增长率
        "net_profit_qoq_growth",         # 净利润环比增长率
    ],

    # ==================== 成本构成（详细分解）====================
    "营业成本构成": [
        "operating_expense_cost_of_sales",           # 营业成本
        "operating_expense_selling_expenses",        # 销售费用
        "operating_expense_administrative_expenses", # 管理费用
        "operating_expense_rnd_expenses",            # 研发费用
        "operating_expense_financial_expenses",      # 财务费用
    ],

    "费用构成": [
        "operating_expense_selling_expenses",        # 销售费用
        "operating_expense_administrative_expenses", # 管理费用
        "operating_expense_rnd_expenses",            # 研发费用
        "operating_expense_financial_expenses",      # 财务费用
    ],

    "成本费用": [
        "operating_expense_selling_expenses",        # 销售费用
        "operating_expense_administrative_expenses", # 管理费用
        "operating_expense_rnd_expenses",            # 研发费用
        "operating_expense_financial_expenses",      # 财务费用
        "operating_expense_cost_of_sales",           # 营业成本
    ],

    # ==================== 现金流构成（详细分解）====================
    "现金流构成": [
        "operating_cf_net_amount",       # 经营性现金流净额
        "investing_cf_net_amount",       # 投资性现金流净额
        "financing_cf_net_amount",       # 筹资性现金流净额
    ],

    # ==================== 资产构成（详细分解）====================
    "资产构成": [
        "asset_cash_and_cash_equivalents",  # 货币资金
        "asset_accounts_receivable",        # 应收账款
        "asset_inventory",                  # 存货
        "asset_total_assets",               # 资产总计
    ],

    # ==================== 负债构成（详细分解）====================
    "负债构成": [
        "liability_short_term_loans",     # 短期借款
        "liability_accounts_payable",     # 应付账款
        "liability_total_liabilities",    # 负债合计
    ],

    # ==================== 利润构成（详细分解）====================
    "利润构成": [
        "total_operating_revenue",        # 营业总收入
        "total_operating_expenses",       # 营业总成本
        "operating_profit",               # 营业利润
        "total_profit",                   # 利润总额
        "net_profit",                     # 净利润
    ],

    # ==================== 收入构成（详细分解）====================
    "收入构成": [
        "total_operating_revenue",        # 营业总收入
        "operating_expense_cost_of_sales", # 营业成本
        "gross_profit_margin",            # 销售毛利率
    ],
})

# 中文别名 → 标准概念组名（用于层级展开）
CONCEPT_ALIASES = {
    # 盈利能力相关
    "盈利能力对比": "盈利能力",
    "盈利能力分析": "盈利能力",
    "盈利分析": "盈利能力",
    "盈利对比": "盈利能力",
    "盈利指标": "盈利能力",

    # 偿债能力相关
    "偿债能力对比": "偿债能力",
    "偿债能力分析": "偿债能力",
    "偿债分析": "偿债能力",
    "偿债对比": "偿债能力",
    "偿债指标": "偿债能力",

    # 运营能力相关
    "运营能力对比": "运营能力",
    "运营能力分析": "运营能力",
    "运营分析": "运营能力",
    "运营对比": "运营能力",
    "运营指标": "运营能力",

    # 成长能力相关
    "成长能力对比": "成长能力",
    "成长能力分析": "成长能力",
    "成长分析": "成长能力",
    "成长对比": "成长能力",
    "成长指标": "成长能力",
    "增长能力": "成长能力",
    "增长能力对比": "成长能力",
    "增长能力分析": "成长能力",

    # 成本构成相关
    "成本构成分析": "营业成本构成",
    "成本分析": "营业成本构成",
    "成本构成": "营业成本构成",
    "营业成本分析": "营业成本构成",

    # 费用构成相关
    "费用分析": "费用构成",
    "费用构成": "费用构成",
    "费用构成分析": "费用构成",
    "三费分析": "费用构成",

    # 现金流构成相关
    "现金流分析": "现金流构成",
    "现金流构成": "现金流构成",
    "现金流构成分析": "现金流构成",
    "现金流量分析": "现金流构成",

    # 资产构成相关
    "资产分析": "资产构成",
    "资产构成": "资产构成",
    "资产构成分析": "资产构成",
    "资产结构": "资产构成",

    # 负债构成相关
    "负债分析": "负债构成",
    "负债构成": "负债构成",
    "负债构成分析": "负债构成",
    "负债结构": "负债构成",

    # 利润构成相关
    "利润分析": "利润构成",
    "利润构成": "利润构成",
    "利润构成分析": "利润构成",
    "利润结构": "利润构成",

    # 收入构成相关
    "收入分析": "收入构成",
    "收入构成": "收入构成",
    "收入构成分析": "收入构成",
    "收入结构": "收入构成",
}


class FinKGResolver:
    """轻量级财务知识图谱 Resolver"""

    def resolve(self, indicators: list, question: str = "") -> dict:
        """
        主入口：解析指标列表，返回标准化结果

        输入: ["税后利润", "盈利能力"]
        输出: {
            "indicators": ["净利润", "销售毛利率", "销售净利率", ...],
            "indicators_en": ["net_profit", "gross_profit_margin", ...],
            "expanded": {"盈利能力": ["销售毛利率", "销售净利率", "ROE", "加权ROE"]},
            "synonym_map": {"税后利润": "净利润"},
        }
        """
        result_indicators = []       # 标准中文名
        result_indicators_en = []    # 英文字段名
        expanded = {}                # 展开记录
        synonym_map = {}             # 同义词映射记录

        for ind in indicators:
            # Step 1: 同义词归一
            resolved_cn = self._resolve_synonym(ind)
            if resolved_cn != ind:
                synonym_map[ind] = resolved_cn

            # Step 2: 层级展开
            children = self._expand_hierarchy(resolved_cn, question)
            if children:
                # 概念组 → 展开为多个子指标
                expanded[resolved_cn] = children
                for child_cn in children:
                    if child_cn not in result_indicators:
                        result_indicators.append(child_cn)
                        child_en = self._cn_to_en(child_cn)
                        if child_en:
                            result_indicators_en.append(child_en)
            else:
                # 单个指标
                if resolved_cn not in result_indicators:
                    result_indicators.append(resolved_cn)
                    en = self._cn_to_en(resolved_cn)
                    if en:
                        result_indicators_en.append(en)

        # 如果同义词映射后仍没有英文名，用 field_matcher 兜底
        final_en = []
        for cn, en in zip(result_indicators, result_indicators_en):
            if en:
                final_en.append(en)
            else:
                match = field_match(cn)
                if match and match[0].get("score", 0) >= 0.7:
                    final_en.append(match[0]["column_en"])
                else:
                    final_en.append(cn)  # 保留原名

        return {
            "indicators": result_indicators,
            "indicators_en": final_en,
            "expanded": expanded,
            "synonym_map": synonym_map,
        }

    def _resolve_synonym(self, text: str) -> str:
        """P1.1: 同义词归一"""
        # 精确匹配
        if text in SYNONYMS:
            return SYNONYMS[text]
        # field_dict 精确匹配（已经是标准名）
        for field_name, info in SCHEMA_DICT.items():
            if text in info.get("synonyms", []):
                return info["label"]
        return text

    def _expand_hierarchy(self, text: str, question: str = "") -> list:
        """P1.2: 层级展开，返回子指标中文名列表，非层级指标返回空列表"""
        # 直接匹配层级名
        if text in HIERARCHY:
            fields_en = HIERARCHY[text]
            return [self._en_to_cn(f) for f in fields_en]

        # 别名匹配
        for alias, standard in CONCEPT_ALIASES.items():
            if alias in text or text in alias:
                if standard in HIERARCHY:
                    fields_en = HIERARCHY[standard]
                    return [self._en_to_cn(f) for f in fields_en]

        # 问题中包含层级关键词
        if question:
            for concept_name, fields_en in HIERARCHY.items():
                if concept_name in question:
                    return [self._en_to_cn(f) for f in fields_en]

        return []

    def _cn_to_en(self, cn_name: str) -> str:
        """中文字段名 → 英文字段名"""
        for field_name, info in SCHEMA_DICT.items():
            if info["label"] == cn_name:
                return field_name
            if cn_name in info.get("synonyms", []):
                return field_name
        return ""

    def _en_to_cn(self, en_name: str) -> str:
        """英文字段名 → 中文字段名"""
        if en_name in SCHEMA_DICT:
            return SCHEMA_DICT[en_name]["label"]
        return en_name


# ============================================================
# 单例
# ============================================================

_resolver = None

def get_kg_resolver():
    global _resolver
    if _resolver is None:
        _resolver = FinKGResolver()
    return _resolver


# ============================================================
# T8 (A2) 只读导出 - 供 PromptManager 构建"可用指标知识"上下文
# 约束：本段只输出 field_dict / 本模块数据的"派生视图"，不复制表；
#       展示用的标准名一律在调用时从 SCHEMA_DICT 实时解析，保证字段零漂移。
# ============================================================

# 归一示例候选用词（仅决定"展示哪些口语词与展示顺序"；标准名由真相源现算，
# 词一旦从真相源消失会被自动跳过，不会输出过期映射 - 全表 256+77 条同义词
# 不整表灌入 prompt，只精选少数几条作规则示例）。
EXEMPLAR_ALIAS_POOL = [
    "税后利润", "营收", "毛利率", "净利率", "每股盈利", "ROE",
    "净收益", "研发支出", "总成本", "总负债", "库存", "净利",
]


def standard_indicator_labels():
    """收敛后的标准指标清单：field_dict.SCHEMA_DICT 全量中文标签（保持插入序）。"""
    return [info["label"] for info in SCHEMA_DICT.values()]


def _alias_standard_name(alias: str) -> str:
    """口语别名 → 标准中文指标名（与 resolver._resolve_synonym 同一归一口径）。"""
    if alias in SYNONYMS:
        return SYNONYMS[alias]
    for info in SCHEMA_DICT.values():
        if alias in info.get("synonyms", []):
            return info["label"]
    return alias


def synonym_norm_examples(limit: int = 6):
    """精选"口语/别名 → 标准指标"展示对（默认最多 6 对，标准名去重）。"""
    if limit <= 0:
        return []
    pairs = []
    seen_canonical = set()
    for alias in EXEMPLAR_ALIAS_POOL:
        if len(pairs) >= limit:
            break
        canonical = _alias_standard_name(alias)
        if canonical and canonical != alias and canonical not in seen_canonical:
            seen_canonical.add(canonical)
            pairs.append((alias, canonical))
    return pairs


def hierarchy_expansions():
    """概念组展开视图：[(概念组名, [子指标中文标签, ...]), ...]（保持 HIERARCHY 序）。

    子指标英文字段一律经 SCHEMA_DICT 转中文标签；字段不存在则跳过，
    保证输出的每一条都指向真相源中仍存在的字段。
    """
    out = []
    for name, fields_en in HIERARCHY.items():
        children = []
        for en_name in fields_en:
            info = SCHEMA_DICT.get(en_name)
            if info:
                children.append(info["label"])
        if children:
            out.append((name, children))
    return out


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    kg = get_kg_resolver()

    tests = [
        # P1.1 同义词
        (["税后利润", "营收"], "金花2023净利润"),
        (["主营收入", "净收益"], ""),
        (["归母净利润", "营业总收入"], ""),
        (["ROE", "毛利率"], ""),

        # P1.2 层级展开
        (["盈利能力"], "白云山与云南白药盈利能力对比"),
        (["偿债能力"], "白云山偿债能力分析"),
        (["运营能力"], "白云山运营能力分析"),
        (["成长能力"], "白云山成长能力分析"),
        (["营业成本构成"], "营业成本构成分析"),
        (["费用构成"], "费用构成分析"),
        (["现金流构成"], ""),
        (["资产构成"], ""),
        (["负债构成"], ""),

        # 混合
        (["税后利润", "盈利能力", "营收"], ""),
        (["净利润", "销售毛利率"], ""),

        # 别名测试
        (["盈利分析"], ""),
        (["偿债对比"], ""),
        (["成本分析"], ""),
    ]

    for indicators, question in tests:
        result = kg.resolve(indicators, question)
        print(f"输入: {indicators}")
        print(f"  同义词: {result['synonym_map']}")
        print(f"  展开: {result['expanded']}")
        print(f"  输出指标: {result['indicators']}")
        print(f"  英文字段: {result['indicators_en']}")
        print()
