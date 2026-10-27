# -*- coding: utf-8 -*-
"""field_mapper.py: 多维相似度语义归并 + 同义词池动态学习"""
import json, os, re
from difflib import SequenceMatcher as SM
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STANDARD = {}
sp = os.path.join(BASE, "data", "schema_columns.json")
if os.path.exists(sp):
    s = json.load(open(sp, encoding="utf-8"))
    for tn, ti in s.get("tables",{}).items():
        for c in ti.get("columns",[]):
            e = {"f": c["en"], "t": tn}
            STANDARD[c["cn"]] = e
            for a in c.get("aliases",[]): STANDARD[a] = e
EXTRA = {
    "营业收入":{"f":"total_operating_revenue","t":"income_sheet"},
    "营业总收入":{"f":"total_operating_revenue","t":"income_sheet"},
    "营收":{"f":"total_operating_revenue","t":"income_sheet"},
    "归属于上市公司股东的净利润":{"f":"net_profit","t":"income_sheet"},
    "归属于母公司股东的净利润":{"f":"net_profit","t":"income_sheet"},
    "归母净利润":{"f":"net_profit","t":"income_sheet"},
    "净利润":{"f":"net_profit","t":"income_sheet"},
    "扣非净利润":{"f":"net_profit_excl_non_recurring","t":"core_performance_indicators_sheet"},
    "总资产":{"f":"asset_total_assets","t":"balance_sheet"},
    "资产总计":{"f":"asset_total_assets","t":"balance_sheet"},
    # 返工14C: "归属于上市公司股东的净资产"是归母口径≠合计口径，不直接映射 equity_total_equity
    # 改由 match() 内兜底：无"所有者权益合计"时才降级匹配
    "净资产":{"f":"equity_total_equity","t":"balance_sheet"},
    "所有者权益合计":{"f":"equity_total_equity","t":"balance_sheet"},
    # 返工12: PDF常见变体 — 括号内注明"或股东权益"
    "所有者权益（或股东权益）合计":{"f":"equity_total_equity","t":"balance_sheet"},
    "所有者权益合计（或股东权益合计）":{"f":"equity_total_equity","t":"balance_sheet"},
    # 返工14: 部分公司(如600332白云山)只用"股东权益合计"
    "股东权益合计":{"f":"equity_total_equity","t":"balance_sheet"},
    "负债合计":{"f":"liability_total_liabilities","t":"balance_sheet"},
    "总负债":{"f":"liability_total_liabilities","t":"balance_sheet"},
    "经营活动产生的现金流量净额":{"f":"operating_cf_net_amount","t":"cash_flow_sheet"},
    "经营现金流":{"f":"operating_cf_net_amount","t":"cash_flow_sheet"},
    "投资现金流":{"f":"investing_cf_net_amount","t":"cash_flow_sheet"},
    "筹资现金流":{"f":"financing_cf_net_amount","t":"cash_flow_sheet"},
    "基本每股收益":{"f":"eps","t":"core_performance_indicators_sheet"},
    "稀释每股收益":{"f":"eps","t":"core_performance_indicators_sheet"},
    "加权平均净资产收益率":{"f":"roe_weighted_excl_non_recurring","t":"core_performance_indicators_sheet"},
    "ROE":{"f":"roe_weighted_excl_non_recurring","t":"core_performance_indicators_sheet"},
    "营业成本":{"f":"operating_expense_cost_of_sales","t":"income_sheet"},
    "销售费用":{"f":"operating_expense_selling_expenses","t":"income_sheet"},
    "管理费用":{"f":"operating_expense_administrative_expenses","t":"income_sheet"},
    "财务费用":{"f":"operating_expense_financial_expenses","t":"income_sheet"},
    "研发费用":{"f":"operating_expense_rnd_expenses","t":"income_sheet"},
    "货币资金":{"f":"asset_cash_and_cash_equivalents","t":"balance_sheet"},
    "应收账款":{"f":"asset_accounts_receivable","t":"balance_sheet"},
    "存货":{"f":"asset_inventory","t":"balance_sheet"},
    "短期借款":{"f":"liability_short_term_loans","t":"balance_sheet"},
    "应付账款":{"f":"liability_accounts_payable","t":"balance_sheet"},
    "未分配利润":{"f":"equity_unappropriated_profit","t":"balance_sheet"},
    "利润总额":{"f":"total_profit","t":"income_sheet"},
    "每股净资产":{"f":"net_asset_per_share","t":"core_performance_indicators_sheet"},
    "经营活动产生的现金流量净额":{"f":"operating_cf_net_amount","t":"cash_flow_sheet"},
    "投资活动产生的现金流量净额":{"f":"investing_cf_net_amount","t":"cash_flow_sheet"},
    "筹资活动产生的现金流量净额":{"f":"financing_cf_net_amount","t":"cash_flow_sheet"},
    "归属于上市公司股东的扣除非经常性损益的净利润":{"f":"net_profit_excl_non_recurring","t":"core_performance_indicators_sheet"},
    "归属于母公司股东的扣除非经常性损益的净利润":{"f":"net_profit_excl_non_recurring","t":"core_performance_indicators_sheet"},

}

EXCLUSIONS = {
    '经营活动现金流入小计': None,
    '经营活动现金流出小计': None,
    '投资活动现金流入小计': None,
    '投资活动现金流出小计': None,
    '筹资活动现金流入小计': None,
    '筹资活动现金流出小计': None,
    '现金流入小计': None,
    '现金流出小计': None,
    # P0-2: 短标签拒绝——"合计""总计""小计"无上下文，无法确定财务含义
    '合计': None,
    '总计': None,
    '小计': None,
}

# 返工16修2: 归母权益标签不再进 EXCLUSIONS 硬排除（返工14 曾在此硬排除，导致归母降级失效）。
# 改为：EXCLUSION_RULES 挡住归母→合计口径的正常匹配，match() 兜底阶段再降级映射 equity（第三优先级）。

STANDARD.update(EXTRA)

# ── 同义词池 (动态学习) ─────────────────────────────────
SYNONYM_POOL_PATH = os.path.join(BASE, "data", "synonym_pool.json")
_synonym_pool_loaded = False
_synonym_pool = {}  # {标准字段: [同义词列表]}

def _load_synonym_pool():
    global _synonym_pool, _synonym_pool_loaded
    if _synonym_pool_loaded: return
    _synonym_pool_loaded = True
    if os.path.exists(SYNONYM_POOL_PATH):
        try:
            with open(SYNONYM_POOL_PATH, "r", encoding="utf-8") as f:
                _synonym_pool = json.load(f)
        except:
            _synonym_pool = {}

def _save_synonym_pool():
    try:
        with open(SYNONYM_POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(_synonym_pool, f, ensure_ascii=False, indent=2)
    except:
        pass

def add_to_synonym_pool(std_field, synonym):
    """添加同义词到池中"""
    _load_synonym_pool()
    if std_field not in _synonym_pool:
        _synonym_pool[std_field] = []
    if synonym not in _synonym_pool[std_field]:
        _synonym_pool[std_field].append(synonym)
        _save_synonym_pool()

def get_synonym_score(label, std_field):
    """从同义词池获取匹配分数 (0-25)"""
    _load_synonym_pool()
    synonyms = _synonym_pool.get(std_field, [])
    for syn in synonyms:
        if syn in label or label in syn:
            return 25
    return 0

# ── M3: 频率学习 ────────────────────────────────────────
HISTORY_PATH = os.path.join(BASE, "data", "mapping_history.json")
_history_loaded = False
_mapping_history = {}  # {label: {std_field: {"count":N, "success":N, "total_conf":F}}}

def _load_history():
    global _mapping_history, _history_loaded
    if _history_loaded: return
    _history_loaded = True
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _mapping_history = data.get("observations", {})
        except:
            _mapping_history = {}

def _save_history():
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump({"observations": _mapping_history}, f, ensure_ascii=False, indent=2)
    except:
        pass

def get_freq_score(label, std_key):
    """根据历史映射频率返回 0-25 的评分，考虑count权重"""
    _load_history()
    obs = _mapping_history.get(label, {}).get(std_key)
    if not obs:
        # 初现映射：给基础分5
        return 5
    count = obs.get("count", 0)
    success_rate = obs.get("success", 0) / max(count, 1)
    if count < 3:
        # 样本不足，保守评分
        return 5 + int(success_rate * 10)
    # 样本充足：历史成功率越高分越高，count越高有加成
    base_score = int(success_rate * 20)
    # count加成：count越高，分数越高（最高+5）
    count_bonus = min(5, count // 100)
    return max(0, min(25, base_score + count_bonus))

# ---- 学习模式开关 ----
# 提取期间设为 False，保证结果确定性（同一批 PDF 处理顺序不影响结果）
LEARNING_ENABLED = True


def set_learning_enabled(enabled: bool):
    """控制 learn() 是否写入历史"""
    global LEARNING_ENABLED
    LEARNING_ENABLED = enabled


def learn(label, std_key, matched_conf):
    """记录一次映射观察，高置信度映射加入同义词池"""
    if not LEARNING_ENABLED:
        return  # 只读模式：不记录不写入
    _load_history()
    if label not in _mapping_history:
        _mapping_history[label] = {}
    if std_key not in _mapping_history[label]:
        _mapping_history[label][std_key] = {"count": 0, "success": 0, "total_conf": 0.0}
    obs = _mapping_history[label][std_key]
    obs["count"] += 1
    obs["total_conf"] += matched_conf
    # 高置信度(>=70)视为成功
    if matched_conf >= 70:
        obs["success"] += 1
    # 高置信度(>=85)且样本充足(count>=10)且成功率>=80%才加入同义词池
    if matched_conf >= 85 and obs["count"] >= 10 and obs["success"] / max(obs["count"], 1) >= 0.8:
        add_to_synonym_pool(std_key, label)
    # 每学50次保存一次
    if obs["count"] % 50 == 0:
        _save_history()


def char_overlap(a, b):
    if not a or not b: return 0
    return sum(1 for c in a if c in b) / max(len(a), len(b)) * 100
def edit_sim(a, b):
    return SM(None, a, b).ratio() * 100

# ── P0-2: 比率/growth 字段语义闸 ─────────────────────────
_RATIO_FIELD_HINTS = ('ratio', 'growth', 'margin', 'yoy', 'qoq', 'roe')

def _is_ratio_or_growth_field(field_name):
    """判断标准字段是否为比率/增长率类"""
    return any(h in field_name.lower() for h in _RATIO_FIELD_HINTS)

_RATIO_LABEL_KEYWORDS = ('率', '比', '增长', '同比', '环比', '增幅', '占比', '周转')

def _label_has_ratio_keyword(label):
    """标签是否包含比率/增长率特征词"""
    return any(kw in label for kw in _RATIO_LABEL_KEYWORDS)


# ── 返工3: 标签文本清洗层 ─────────────────────────────────
def _cut_unclosed_bracket(s):
    """修复1: 未闭合括号截断 — "四、利润总额（亏损总额以"—"号" → "四、利润总额" """
    for i, ch in enumerate(s):
        if ch == '（':
            if '）' not in s[i+1:]:
                return s[:i]
            break
        if ch == '(':
            if ')' not in s[i+1:]:
                return s[:i]
            break
    return s


# 修复4 后缀模式（先精确再通用正则）
_SUFFIX_EXACT = ('_本报告期', '_年初至报告期末', '_本年', '_上年', '_本期', '_上期')
_SUFFIX_RE = re.compile(r'_[A-Za-z一-鿿]+$')

# 修复2+3+4 前缀模式（按顺序匹配，只剥第一个命中）
_PREFIX_PATTERNS = [
    re.compile(r'^[一二三四五六七八九十]+、'),          # 修复2: 一、~十、
    re.compile(r'^（[一二三四五六七八九十]+）'),
    re.compile(r'^\([一二三四五六七八九十]+\)'),
    re.compile(r'^\d+[\.、]'),
    # 修复3: 分类前缀（带冒号，防误伤"流动负债合计"这种无冒号的）
    re.compile(r'^(流动资产|非流动资产|流动负债|非流动负债|所有者权益|股东权益合计|负债合计|资产合计)[：:]'),
    # 返工11: 会计常用前缀——"其中：营业成本""加：其他收益""减：所得税费用"
    re.compile(r'^(其中)[：:]'),
    re.compile(r'^(加)[：:]'),
    re.compile(r'^(减)[：:]'),
    # 修复4: 单位前缀
    re.compile(r'^（元/股）'),
    re.compile(r'^（元）'),
    re.compile(r'^（单位：元）'),
]


def preprocess_label(label):
    """返工3: 标签文本清洗 — 修复 PDF 截断/断行/行合并/前缀后缀污染"""
    # 修复5: 断行清洗
    s = label.replace('\n', '')
    # 修复1: 未闭合括号截断
    s = _cut_unclosed_bracket(s)
    # 修复4: 后缀剥离
    for w in _SUFFIX_EXACT:
        if s.endswith(w):
            s = s[:-len(w)]
            break
    else:
        s = _SUFFIX_RE.sub('', s)
    # 修复2+3+4: 前缀剥离（只剥第一个命中）
    for pat in _PREFIX_PATTERNS:
        m = pat.match(s)
        if m:
            s = pat.sub('', s)
            break
    return s

def strip_unit_suffix(label):
    """??"????（?????）"??"（?????）"??
    ?? (?????, ????)
    ????（）???()??
    """
    m = re.search(r'[（(][^）)]*[）)]$', label)
    if m:
        suffix = m.group(0)
        core = label[:m.start()]
        if len(core) >= 2:
            return core.strip(), suffix.strip()
    return label, None


def match(label, context_tbl=None):
    # 返工3: 标签文本清洗 — 修复 PDF 截断/断行/前缀后缀污染
    label = preprocess_label(label)

    if label in EXCLUSIONS:
        return None, 0
    label = label.strip().replace(" ","").replace("　","")
    if not label or len(label) < 2: return None, 0

    # ---- P0修复: 语义排除规则 ----
    # 防止"扣非净利润" → 匹配到"净利润" (子串陷阱)
    # 防止"营业成本"   → 匹配到"营业总收入" (编辑距离陷阱)
    EXCLUSION_RULES = [
        # (标签含, 排除字段含)
        ("扣非", "net_profit"),          # "扣非净利润" ≠ "净利润"
        ("扣非归母", "net_profit"),       # "扣非归母净利润" ≠ "净利润"
        ("归母扣非", "net_profit"),
        ("营业总成本", "operating_expense_cost_of_sales"),  # "营业总成本" ≠ "营业成本"
        ("营业成本", "total_operating_revenue"),            # "营业成本" ≠ "营业总收入"
        ("营业总收入", "operating_expense_cost_of_sales"),  # "营业总收入" ≠ "营业成本"
        ("销售费用", "operating_expense_cost_of_sales"),    # 不同费用科目
        ("管理费用", "operating_expense_cost_of_sales"),
        ("财务费用", "operating_expense_cost_of_sales"),
        ("研发费用", "operating_expense_cost_of_sales"),
        ("营业总支出", "operating_expense_cost_of_sales"),
        # 返工7: 营业外收支 ≠ 营业收支（子串陷阱）
        ("营业外收入", "total_operating_revenue"),             # "营业外收入" ≠ "营业总收入"
        ("营业外支出", "total_operating_expenses"),            # "营业外支出" ≠ "营业总成本"
        ("营业外支出", "operating_expense_cost_of_sales"),     # "营业外支出" ≠ "营业成本"
        # 返工14: 归母权益 ≠ 合计权益
        ("归属于母公司", "equity_total_equity"),
        ("归母", "equity_total_equity"),
        ("母公司所有者权益", "equity_total_equity"),
        ("母公司股东权益", "equity_total_equity"),
    ]
    def _is_excluded(attempt_label, std_key, field_name=None):
        """检查标签是否应该被排除匹配到某个标准字段

        返工17修3: 修复排除规则失效——EXCLUSION_RULES 的 excluded_field 是英文字段名
        (如 "total_operating_revenue")，而旧代码用中文 std_key(如 "营业总收入")去比对，
        永远不相等 → "营业外收入" 错误匹配到 "营业总收入"(000538 营收取错列)。
        改为同时比对英文 field_name。
        """
        for pattern, excluded_field in EXCLUSION_RULES:
            if pattern in attempt_label and excluded_field in (std_key, field_name):
                return True
        # 成本/收入互斥（同时检查英文 field_name 与中文 std_key）
        fname = (field_name or "").lower()
        has_cost = any(kw in attempt_label for kw in ["成本", "费用", "支出"])
        has_revenue = any(kw in attempt_label for kw in ["收入", "营收"])
        if has_cost and ("revenue" in fname or "收入" in std_key):
            return True
        if has_revenue and ("cost" in fname or "成本" in std_key or "费用" in std_key):
            return True
        return False

    clean_label, unit_suffix = strip_unit_suffix(label)

    best, best_score, best_std, best_used_clean = None, 0, None, False
    candidates = [(label, False), (clean_label, True)] if clean_label != label else [(label, False)]

    for attempt_label, is_clean in candidates:
        for std, entry in STANDARD.items():
            if not std: continue

            # ---- P0: 语义排除检查 ----
            if _is_excluded(attempt_label, std, entry.get("f")):
                continue

            lit = max(char_overlap(attempt_label, std), edit_sim(attempt_label, std)) * 0.3
            if attempt_label == std:
                score = 100 if is_clean else 100
                if score > best_score or (score == best_score and len(std) > len(best_std or "")):
                    best, best_score, best_std, best_used_clean = entry, score, std, is_clean
                continue

            # ????
            if std in attempt_label or attempt_label in std:
                lit = max(lit, 25)

            # 同义词匹配 (从同义词池动态学习，替代硬编码)
            syn = get_synonym_score(attempt_label, std)

            # 前缀模糊匹配：去掉常见前缀后再匹配
            prefix_bonus = 0
            if syn == 0:  # 同义词池没命中才做前缀匹配
                common_prefixes = [
                    "项目", "期末", "期初", "本年", "上年", "本期", "上期",
                    # 返工B: 会计常用前缀——"其中：营业成本""加：其他收益""减：所得税费用"
                    "其中：", "加：", "减：",
                    "其中:", "加:", "减:",
                ]
                for prefix in common_prefixes:
                    if attempt_label.startswith(prefix):
                        stripped = attempt_label[len(prefix):]
                        # 剥离前缀后，再去掉可能残留的冒号
                        if stripped and stripped[0] in ('：', ':'):
                            stripped = stripped[1:]
                        if len(stripped) >= 2:
                            # 去掉前缀后和标准字段比较
                            stripped_lit = max(char_overlap(stripped, std), edit_sim(stripped, std)) * 0.3
                            if std in stripped or stripped in std:
                                stripped_lit = max(stripped_lit, 25)
                            if stripped_lit > 20:
                                prefix_bonus = max(prefix_bonus, 15)
                            break

            # 上下文表匹配 (更平衡的奖励)
            if context_tbl and entry["t"] == context_tbl:
                ctx = 15  # 同表加分降低，避免过度偏向
            elif context_tbl and entry["t"] != context_tbl:
                ctx = -10  # 跨表惩罚降低，避免过度排斥
            else:
                ctx = 5

            # ???????????
            # 频率学习分数（M3）: 优先从历史获取，兜底硬编码
            freq_learned = get_freq_score(attempt_label, std)
            freq_hardcoded = 20 if any(kw in std for kw in ["营业收入","净利润","总资产","净资产"]) else 10
            freq = max(freq_learned, freq_hardcoded) if is_clean else freq_hardcoded

            # ????????????????????
            clean_bonus = 10 if is_clean else 0

            # P0-2: 比率/growth字段语义闸 — 标签无比率特征词 → 强惩罚
            ratio_penalty = 0
            if _is_ratio_or_growth_field(entry['f']) and not _label_has_ratio_keyword(attempt_label):
                ratio_penalty = -30

            total = lit + syn + ctx + freq + clean_bonus + prefix_bonus + ratio_penalty
            if total > best_score or (total == best_score and len(std) > len(best_std or "")):
                best, best_score, best_std, best_used_clean = entry, total, std, is_clean

    # 严格准入：子串包含关系用40阈值，否则提高到55
    if best:
        is_substring = (best_std in label or label in best_std) if best_std else False
        min_threshold = 40 if is_substring else 55

        # P0-2: 短标签(<4字)子串匹配 → 阈值提高到70
        if is_substring and len(label) < 4:
            min_threshold = 70

        if best_score >= min_threshold:
            return best, round(best_score, 1)

    # 返工16修2: equity 逐级降级兜底 — 前一项缺失时降级，不许直接丢
    # 优先级: 所有者权益（或股东权益）合计(conf100) > 归属于上市公司股东的净资产(conf60) > 归属于母公司股东权益合计(conf50)
    if best is None and any(kw in label for kw in ("归属于上市公司股东", "归属于母公司", "归母", "母公司")) and \
       ("净资产" in label or "权益" in label):
        for std, entry in STANDARD.items():
            if entry.get("f") == "equity_total_equity" and std in ("净资产", "所有者权益合计"):
                # 归属于上市公司股东的净资产 → 第二优先级(60)
                if "归属于上市公司股东" in label and "净资产" in label:
                    return entry, (60.0 if (entry["t"] == context_tbl or context_tbl is None) else 55.0)
                # 归属于母公司/归母 → 第三优先级(50)
                return entry, (50.0 if (entry["t"] == context_tbl or context_tbl is None) else 45.0)

    return None, 0
