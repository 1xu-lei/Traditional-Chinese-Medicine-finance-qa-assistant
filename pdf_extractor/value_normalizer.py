# -*- coding: utf-8 -*-
"""value_normalizer.py: M5 数值标准化 + 量纲统一
改造:
  1. 增强单位检测: 扫描表格上下方的单位声明，识别"单位：万元""单位：千元"等
  2. 跨章节判断: 单位声明和当前表在同一章节内才继承
  3. 混用单位处理: EPS用元，其他用万元时特殊处理
"""
import re

# 单位匹配模式（权重从高到低）
UNIT_PATTERNS = [
    (r'单位[：:　]\s*人民币元\s*\(本表\)?', 1),
    (r'单位[：:　]\s*元币种', 1),                       # "单位：元币种：人民币" 粘连格式
    (r'单位[：:　]\s*元(?![万千亿兆百])', 1),            # 负向断言只排除量级字
    (r'单位[：:　]\s*千元', 1000),
    (r'人民币千元', 1000),
    (r'单位[：:　]\s*万元', 10000),
    (r'人民币万元', 10000),
    (r'单位[：:　]\s*亿元', 100000000),
    (r'人民币亿元', 100000000),
    (r'金额单位[：:　]元', 1),
    (r'金额单位[：:　]千元', 1000),
    (r'金额单位[：:　]万元', 10000),
    (r'金额单位[：:　]亿元', 100000000),
    (r'单位[：:　]人民币元', 1),
    # 常见顺序模式: "以下数据单位均为人民币万元"
    (r'单位均为人民币(?:万|千|亿)?元', None),  # 需进一步解析
    (r'以下数据.*?单位[：:].*?(?:万|千|亿)?元', None),  # 需进一步解析
]

# 进一步解析"以下数据单位为万元"格式
UNIT_IN_TEXT = [
    (r'单位[：:](?:人民币)?(千)元', 1000),
    (r'单位[：:](?:人民币)?(万)元', 10000),
    (r'单位[：:](?:人民币)?(亿)元', 100000000),
    (r'单位均为人民币(千)元', 1000),
    (r'单位均为人民币(万)元', 10000),
    (r'单位均为人民币(亿)元', 100000000),
]

# 章节边界标记
SECTION_BOUNDARIES = [
    "一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、", "十、",
    "（一）", "（二）", "（三）", "（四）", "（五）",
    "利润表", "资产负债表", "现金流量表",
    "主要会计数据", "主要财务数据", "主要财务指标", "财务报表",
]

# 每股类字段（单位是元/股，不参与表格级倍率）
UNIT_FIELDS = {'eps', 'net_asset_per_share', 'operating_cf_per_share'}

# 比率类字段（存小数，不是原始数值）
RATIO_FIELDS = {'roe', 'roe_weighted_excl_non_recurring', 'gross_profit_margin',
    'net_profit_margin', 'operating_cf_ratio_of_net_cf', 'investing_cf_ratio_of_net_cf',
    'financing_cf_ratio_of_net_cf', 'asset_liability_ratio',
    'operating_revenue_yoy_growth', 'operating_revenue_qoq_growth',
    'net_profit_yoy_growth', 'net_profit_qoq_growth',
    'net_profit_excl_non_recurring_yoy',
    'asset_total_assets_yoy_growth', 'liability_total_liabilities_yoy_growth',
    'net_cash_flow_yoy_growth'}


def detect_unit(text):
    """从文本中检测单位声明，返回 (倍率因子, 是否找到声明)
    返回 (factor, True) 表示找到了明确的单位声明
    返回 (1, False) 表示没找到任何单位声明
    """
    if not text: return (1, False)

    for pat, factor in UNIT_PATTERNS:
        if re.search(pat, text):
            if factor is None:
                for pat2, factor2 in UNIT_IN_TEXT:
                    if re.search(pat2, text): return (factor2, True)
                return (1, True)  # 找到了声明但解析不出倍率
            return (factor, True)

    for pat2, factor2 in UNIT_IN_TEXT:
        if re.search(pat2, text): return (factor2, True)

    return (1, False)


def _has_section_boundary(text_a, text_b):
    """判断两段文本之间是否有章节边界"""
    for marker in SECTION_BOUNDARIES:
        if marker in text_a or marker in text_b:
            return True
    return False


def detect_unit_above(main_text, above_texts, below_texts=None, global_unit_factor=None, global_unit_found=False):
    """扫描单位声明（优先本页，再上下页，最后全局兜底）

    返回: (unit_factor, unit_raw, unit_source_text)
    - unit_factor: 单位倍率
    - unit_raw: 原始单位名称（如"万元"、"元"）
    - unit_source_text: 单位声明原文
    """
    # 先查本页（在section边界之前的部分）
    boundary_pos = len(main_text)
    for marker in SECTION_BOUNDARIES:
        pos = main_text.find(marker)
        if pos != -1 and pos < boundary_pos:
            boundary_pos = pos
    text_before_boundary = main_text[:boundary_pos]
    uf, found, source_text = detect_unit_with_source(text_before_boundary)
    if found: return uf, _get_unit_name(uf), source_text

    # 也检查全文
    uf, found, source_text = detect_unit_with_source(main_text)
    if found: return uf, _get_unit_name(uf), source_text

    # 向上扫描（最多5页，遇到section边界停止）
    for page_text, page_idx in above_texts[:5]:
        has_boundary = any(m in page_text for m in SECTION_BOUNDARIES)
        if has_boundary:
            uf, found, source_text = detect_unit_with_source(page_text)
            if found:
                return uf, _get_unit_name(uf), source_text
            break
        uf, found, source_text = detect_unit_with_source(page_text)
        if found:
            return uf, _get_unit_name(uf), source_text

    # 向下扫描（最多3页）
    if below_texts:
        for page_text, page_idx in below_texts[:3]:
            has_boundary = any(m in page_text for m in SECTION_BOUNDARIES)
            if has_boundary:
                uf, found, source_text = detect_unit_with_source(page_text)
                if found:
                    return uf, _get_unit_name(uf), source_text
                break
            uf, found, source_text = detect_unit_with_source(page_text)
            if found:
                return uf, _get_unit_name(uf), source_text

    # 全局兜底：有明确检测结果就用（不管是不是元），全都没扫到才默认万元
    if global_unit_found:
        return global_unit_factor, _get_unit_name(global_unit_factor), "global_fallback"
    # 默认万元 — 中国上市公司财报的标准单位
    return 10000, "万元", "default_wan"


def detect_unit_with_source(text):
    """从文本中检测单位声明，返回 (倍率因子, 是否找到声明, 声明原文)"""
    if not text: return (1, False, "")

    for pat, factor in UNIT_PATTERNS:
        match = re.search(pat, text)
        if match:
            if factor is None:
                for pat2, factor2 in UNIT_IN_TEXT:
                    match2 = re.search(pat2, text)
                    if match2: return (factor2, True, match2.group(0))
                return (1, True, match.group(0))
            return (factor, True, match.group(0))

    for pat2, factor2 in UNIT_IN_TEXT:
        match = re.search(pat2, text)
        if match: return (factor2, True, match.group(0))

    return (1, False, "")


def _get_unit_name(factor):
    """根据倍率因子返回单位名称"""
    if factor == 1:
        return "元"
    elif factor == 1000:
        return "千元"
    elif factor == 10000:
        return "万元"
    elif factor == 100000000:
        return "亿元"
    else:
        return "未知"


def parse_number(raw):
    """解析数值字符串为float，支持全角括号/负号/数字、%, *, 脚注, 空格等"""
    if raw is None: return None
    t = str(raw).strip()
    if not t or t in ('-', '--', chr(8212), chr(19981)+chr(33046), 'N/A', '', '——'):
        return None

    # 全角数字 → 半角 (１２３ → 123)
    _FULL_TO_HALF = {chr(i): chr(i - 0xFEE0) for i in range(0xFF10, 0xFF1A)}
    t = ''.join(_FULL_TO_HALF.get(c, c) for c in t)

    # 全角负号 → 半角 (－123 → -123)
    t = t.replace(chr(65293), '-')

    is_negative = False
    # 半角括号: (123) → -123
    if t[:1] == '(' and t[-1:] == ')':
        is_negative = True
        t = t[1:-1]
    # 全角括号: （123）→ -123
    elif t[:1] == chr(65288) and t[-1:] == chr(65289):
        is_negative = True
        t = t[1:-1]
    # 尾部负号: 123- → -123 (某些PDF格式)
    elif t[-1:] == '-' and len(t) > 1:
        is_negative = True
        t = t[:-1]

    # 去除: 逗号、全角逗号、空格、*号脚注、%号
    t = t.replace(',', '').replace(chr(65292), '').replace(' ', '')
    t = t.rstrip('*').rstrip(chr(8224))
    t = t.replace('%', '')
    if not t: return None
    try:
        v = float(t)
        return -v if is_negative else v
    except:
        return None


def normalize(raw, unit_factor=1, field_name=None, label=None):
    """数值标准化为万元为单位

    规则:
      - 每股类(eps/nav): 保留元, 不参与倍率
      - 比率类(roe/毛利率等): 百分比数字→小数 (0.75%→0.0075)
      - 其他: 统一转为万元 (v * unit_factor / 10000)
    """
    v = parse_number(raw)
    if v is None: return None

    # 每股类: 不参与表格倍率
    if field_name and field_name in UNIT_FIELDS:
        return round(v, 4)

    # 比率类: 存小数
    if field_name and field_name in RATIO_FIELDS:
        # 事项1: 检测百分比标记 — 财报里比率值写的是百分比数字（如 0.75 表示 0.75%）
        has_pct = ('%' in str(raw)) or (label and ('（%）' in label or '(%)' in label))
        v = round(v, 4)
        if abs(v) > 1000: return None  # P0-4: >100000% 不可能是比率，拒绝
        if has_pct or abs(v) > 1:
            v = v / 100
        return round(v, 6)

    # 普通数值: v * unit_factor / 10000 -> 转为万元
    v = round(v * unit_factor / 10000, 4)
    # P0修复: 不再静默丢弃大值，改为标记但保留
    # 1e12 万元 = 10万亿亿元，实际不可能但保留给上层 validator 判断
    if abs(v) > 1e12:
        return None  # 确认为不可能的值才丢弃
    return v


def normalize_with_page(raw, page_text, field_name=None, label=None):
    """对单页提取的数值标准化"""
    uf, _ = detect_unit(page_text)
    return normalize(raw, uf, field_name, label=label)


def validate_unit_by_magnitude(raw_values, candidate_factor):
    """量级校验：用表格金额类中位数反算，确认单位是否合理

    参数:
      raw_values: 表格原始数值列表（字符串或数字）
      candidate_factor: 候选单位倍率（1=元, 10000=万元, ...）

    返回: (is_reasonable: bool, normalized_median: float)
      - is_reasonable: 归一化后中位数是否落在合理范围
      - normalized_median: 归一化后的中位数（万元）

    原理: 中药上市公司，单个金额字段的合理范围约 1分 ~ 5000亿元(万元)
          即 0.01 ~ 5e7 万元

    重要: 只对"金额型"原始值（abs>=100）算中位数，排除 EPS(0.01)/ROE(0.4) 等
          小数，避免混合单位表（摘要表）的中位数被小数拉低导致单位误判。
    """
    if not raw_values:
        return True, 0  # 没数据时不推翻单位

    parsed = []
    for v in raw_values:
        p = parse_number(v)
        if p is not None and abs(p) > 0:
            parsed.append(p)
    if len(parsed) < 3:
        return True, 0  # 样本不足，不推翻

    # 返工2: 只取"金额型"值（PDF原始值 >= 100），排除 EPS/ROE 等小数
    amount_parsed = [p for p in parsed if abs(p) >= 100]
    if len(amount_parsed) == 0:
        return True, 0  # 无金额型样本，不推翻单位
    if len(amount_parsed) == 1:
        # P0修复: 单样本用"金额下限"判断，防止摘要表金额样本不足时单位误判。
        # 摘要表(主要会计数据)的唯一大金额值通常是营收/总资产，归一化后应 >= 100万元；
        # 若 < 100万元说明该单位错误(如"元"误判导致缩小10000倍)。
        p = amount_parsed[0]
        normalized_single = p * candidate_factor / 10000
        return abs(normalized_single) >= 100, round(normalized_single, 2)

    amount_parsed.sort()
    median_raw = amount_parsed[len(amount_parsed) // 2]
    normalized_median = median_raw * candidate_factor / 10000

    # 合理范围: 1分 ~ 5000亿元（万元）
    MIN_REASONABLE = 0.01
    MAX_REASONABLE = 5e7

    is_reasonable = MIN_REASONABLE <= abs(normalized_median) <= MAX_REASONABLE
    return is_reasonable, round(normalized_median, 2)


def find_best_unit(raw_values, local_factor, global_factor, global_found):
    """从候选单位中选量级最合理的

    候选顺序: 局部检测 → 全局检测 → 元 → 万元
    取第一个量级合理的，都不合理就保留局部检测结果

    返回: (best_factor, best_unit_name, source)
    """
    candidates = []
    # 局部检测
    candidates.append((local_factor, _get_unit_name(local_factor), "local"))
    # 全局检测（如果有）
    if global_found and global_factor != local_factor:
        candidates.append((global_factor, _get_unit_name(global_factor), "global"))
    # 兜底候选
    for fallback_factor, fallback_name in [(1, "元"), (10000, "万元")]:
        if fallback_factor not in [c[0] for c in candidates]:
            candidates.append((fallback_factor, fallback_name, "fallback"))

    for factor, name, source in candidates:
        ok, norm_median = validate_unit_by_magnitude(raw_values, factor)
        if ok:
            selected_source = f"{source}_magnitude_ok(median={norm_median}万元)"
            # P1-4: 单位冲突时记录双方
            if global_found and global_factor != local_factor:
                if factor != local_factor:
                    print(f"  [单位冲突] 局部={_get_unit_name(local_factor)} 全局={_get_unit_name(global_factor)} → 量级选中={name}")
            return factor, name, selected_source

    # P1-4: 都不合理也记录
    if global_found and global_factor != local_factor:
        print(f"  [单位冲突] 局部={_get_unit_name(local_factor)} 全局={_get_unit_name(global_factor)} → 均不合理，保留局部")
    # 都不合理，保留局部检测结果（让上层 validator 处理）
    return local_factor, _get_unit_name(local_factor), "local_all_unreasonable"
