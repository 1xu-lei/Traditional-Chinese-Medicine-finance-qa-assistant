# -*- coding: utf-8 -*-
"""table_extractor.py: 分块提取 + 评分回退
v3: 先扫描文档结构定位section，再在对应section内提取表格
"""
import pdfplumber, re
from collections import OrderedDict

# ── 页文本缓存：避免同一页 extract_text() 被重复调用 ─────────
class _PageTextCache:
    """缓存 PDF 每页的 extract_text() 结果，处理完一份 PDF 即释放"""
    def __init__(self, pdf):
        self._pdf = pdf
        self._cache = {}

    def get(self, page_idx):
        """返回页文本，首次访问时提取并缓存"""
        if page_idx not in self._cache:
            try:
                self._cache[page_idx] = self._pdf.pages[page_idx].extract_text() or ""
            except IndexError:
                self._cache[page_idx] = ""
        return self._cache[page_idx]

    def total_pages(self):
        return len(self._pdf.pages)

TABLE_PROFILES = {
    "income_sheet": {
        "cn": "利润表", "section": ["利润表","二、"],
        "must_kw": ["营业收入","营业总收入","净利润"],
        "weight_kw": OrderedDict([
            ("营业总收入",30),("营业收入",28),("营业成本",15),
            ("营业利润",10),("利润总额",15),("净利润",25),
            ("归属于上市公司股东的净利润",25),
            ("销售费用",5),("管理费用",5),("财务费用",5),("研发费用",5),
        ]),
        "threshold":60,"exit_threshold":85,
        "section_markers": ["合并利润表", "利润表"],
    },
    "balance_sheet": {
        "cn": "资产负债表", "section": ["资产负债表","一、"],
        "must_kw": ["资产总计","总资产"],
        "weight_kw": OrderedDict([
            ("货币资金",15),("应收账款",8),("存货",8),
            ("流动资产",10),("固定资产",8),
            ("资产总计",25),
            ("负债合计",25),("总负债",20),
            ("所有者权益合计",25),("净资产",20),
            ("归属于上市公司股东的净资产",20),
            ("短期借款",8),("应付账款",8),("未分配利润",12),
        ]),
        "threshold":60,"exit_threshold":85,
        "section_markers": ["合并资产负债表", "资产负债表"],
    },
    "cash_flow_sheet": {
        "cn": "现金流量表", "section": ["现金流量表","三、"],
        "must_kw": ["经营活动"],
        "weight_kw": OrderedDict([
            ("经营活动产生的现金流量净额",28),("经营活动现金",22),
            ("投资活动产生的现金流量净额",18),("投资活动现金",15),
            ("筹资活动产生的现金流量净额",18),("筹资活动现金",15),
            ("现金及现金等价物",12),("现金净流量",15),
        ]),
        "threshold":55,"exit_threshold":80,   # 返工2: 50→55 防摘要表误识别
        "section_markers": ["合并现金流量表", "现金流量表"],
    },
    "core_performance_indicators_sheet": {
        "cn": "核心业绩指标",
        "section": ["主要会计数据","主要财务数据","主要财务指标"],
        "must_kw": ["每股收益","基本每股收益"],
        "weight_kw": OrderedDict([
            ("基本每股收益",25),("稀释每股收益",18),("每股收益",20),
            ("净资产收益率",18),("加权平均净资产收益率",22),
            ("归属于上市公司股东的净资产",18),("总资产",12),
            ("经营活动产生的现金流量净额",12),("每股净资产",12),
            ("营业收入",10),("营业总收入",10),
            ("净利润",15),("归属于上市公司股东的净利润",15),
            ("扣非净利润",15),
        ]),
        "threshold":55,"exit_threshold":82,
        "section_markers": ["主要会计数据", "主要财务数据", "主要财务指标"],
    },
}

SUMMARY_KW = ["营业收入","净利润","每股收益","总资产","净资产","加权平均净资产收益率"]

# 每张表的section结束标记（遇到这些说明下一张表开始了）
SECTION_END_MARKERS = {
    "income_sheet": ["资产负债表", "现金流量表", "主要会计数据", "主要财务数据",
                     "母公司利润表", "利润表（母公司）",    # 返工9: 母公司表边界
                     "母公司损益表", "损益表（母公司）"],
    "balance_sheet": ["利润表", "现金流量表", "主要会计数据", "主要财务数据",
                      "母公司资产负债表", "资产负债表（母公司）"],
    "cash_flow_sheet": ["利润表", "资产负债表", "主要会计数据", "主要财务数据",
                        "母公司现金流量表", "现金流量表（母公司）"],
    "core_performance_indicators_sheet": ["利润表", "资产负债表", "现金流量表",
                                          "母公司利润表", "母公司资产负债表"],
}

# 返工15修3: 母公司表标题(精确章节词)——用于整页搜索边界，不受"前4行"限制
PARENT_TITLE_KW = {
    "income_sheet": ["母公司利润表", "利润表（母公司）", "母公司损益表", "损益表（母公司）"],
    "balance_sheet": ["母公司资产负债表", "资产负债表（母公司）"],
    "cash_flow_sheet": ["母公司现金流量表", "现金流量表（母公司）"],
}

# 返工16修1: 同页多表时，优先选含"核心尾部行"的表。
# 合并表跨页续表的最后一页(负债合计/所有者权益/净利润等)常与"母公司表"标题同页，
# 而母公司表带"项目"表头会被 header_score+25 抢占。用核心尾部关键词锁定真正的合并表尾部。
TABLE_CORE_TAIL_KW = {
    "balance_sheet": ["负债合计", "所有者权益", "负债和所有者权益"],
    "income_sheet": ["净利润", "利润总额"],
    "cash_flow_sheet": ["现金及现金等价物", "期末现金"],
    "core_performance_indicators_sheet": ["加权平均净资产收益率"],
}


# ==== 返工22: CPI 路径专用修正（实测根因，见 D:\lunw\_dbg_cpi.py） ====
CPI_TT = "core_performance_indicators_sheet"

# 修法1: CPI 是 1~2 页的小表，不能按三表的"向后扩5页"规则贪婪扩展。
# 实测 600080_2023Q1: 起始页 p0 提取正确(8行/双must_kw)，却被扩到 [0,1,2,3,4]，
# 把 p1 变动原因表 + p3/p4 资产负债表整体并进 CPI，真值反被列错位打空。
CPI_MAX_SPAN = 2
# 续页整页命中这些词 = 已翻到隔壁表，停止扩展（三表不受影响）
CPI_STOP_KW = ["变动情况", "变动的原因", "变动原因", "主要原因", "变动比例",
               "非经常性损益项目", "股东总数", "前10名股东", "前十名股东",
               "释义", "分季度", "季度数据"]

# 修法3: CPI 标签粘连清洗——"本期比上年同期增减"列的说明文字被并进标签列，
# 导致 field_mapper 子串匹配降权。实测:
#   "基本每股收益（元／股）不适用"        conf=50  → 清洗后 conf=100
#   "加权平均净资产收益率（%）增加 33.3"  conf=60  → 清洗后 conf=100
#   "归属于上市公司股东的净利润不适用"    conf=55.5 且被误映射到"扣非净利润"
# 只作用于 CPI 路径，三表标签不碰。
_CPI_LABEL_NOISE = [
    re.compile(r"(增加|减少|下降|上升)\s*[\d.,]+\s*个百分点.*$"),
    re.compile(r"(增加|减少|下降|上升)\s*[\d.,]+\s*%?$"),
    re.compile(r"(增加|减少|下降|上升)\s*$"),
    re.compile(r"不适用$"),
    re.compile(r"持平$"),
]


def _clean_cpi_label(label):
    """清洗 CPI 标签列里粘连的"增减"说明文字。返回清洗后的标签。"""
    s = re.sub(r"\s+", "", str(label or ""))
    if not s:
        return s
    for _ in range(3):  # 可能叠加多段噪声
        before = s
        for pat in _CPI_LABEL_NOISE:
            s = pat.sub("", s)
        s = s.strip(" 　:：,，")
        if s == before:
            break
    return s


def _clean_cpi_matrix_labels(matrix):
    """对 CPI matrix 的标签列做清洗（浅拷贝，不改原对象）。"""
    if not matrix or not matrix.get("data_rows"):
        return matrix
    new_rows = []
    n_cleaned = 0
    n_dropped = 0
    for row in matrix["data_rows"]:
        if not row:
            new_rows.append(row)
            continue
        cleaned = _clean_cpi_label(row[0])
        # 返工22修3b: "扣除非经常性损益后的基本/稀释每股收益" 库里没有对应字段，
        # 而 field_mapper 会把它子串匹配到 net_profit_excl_non_recurring(扣非净利润)
        # 且 conf=60.7 刚好过闸 → 把"每股 -0.05 元"写成"扣非净利润 -0.0000 万元"。
        # 清洗后置信度会升高，必须同时把这类无字段可落的行丢掉。
        if "每股收益" in cleaned and ("扣除非经常性损益" in cleaned or "扣非" in cleaned):
            n_dropped += 1
            continue
        if cleaned != str(row[0] or "").strip():
            n_cleaned += 1
            row = [cleaned] + list(row[1:])
        new_rows.append(row)
    if n_cleaned or n_dropped:
        matrix = dict(matrix)
        matrix["data_rows"] = new_rows
        print("  [CPI标签清洗] 修正 %d 行粘连标签, 丢弃 %d 行扣非每股收益" % (n_cleaned, n_dropped))
    return matrix


_PROSE_PUNCT = "。；;，"


def _cell_is_prose(s):
    """判断单元格是否为叙述性文字(句子)而非科目标签/数值。

    财务报表科目标签不含句读标点(顿号"、"在"销售商品、提供劳务收到的现金"里合法，
    故不计入)，长度也很少超过 40 字；管理层讨论正文两者必占其一。
    """
    s = str(s or "")
    return len(s) > 40 or any(ch in s for ch in _PROSE_PUNCT)


def _value_col_index(matrix):
    """修法2: 返回该页数据行中"纯数值单元格"最多的值列下标(跳过标签列0)。

    用于跨页合并前的列结构兼容性判断——只拦"目标值列位置不一致"的页，
    同结构的正常跨页续表照常合并。无可判定值列时返回 None(不参与拦截)。
    """
    rows = matrix.get("data_rows", []) if matrix else []
    if not rows:
        return None
    counts = {}
    for row in rows:
        for ci, cell in enumerate(row[1:], start=1):
            if _is_pure_amount_cell(str(cell).rstrip("%")):
                counts[ci] = counts.get(ci, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]


def _is_cpi_foreign_table(matrix):
    """返工25: 判断该页矩阵是否属于"其他表"(变动原因表/资产负债表/股东表/OCI表/释义表)。

    CPI"主要会计数据"表跨 2 页时，流量页(营收/净利, 累计列=3/5)与比率/时点页
    (每股收益/ROE/总资产, 本报告期列=1)的"最数值列"天然不同。旧列闸按值列位置
    丢页会误删流量页 → 表头继承丢失 → 续页首行被提升成坏表头。
    改为语义过滤: 只剔除"明确是其他表"的页，真 CPI 表(含无表头续页)全保留。
    """
    hdr = "".join(str(c or "") for c in (matrix.get("header_row", []) or []))
    # 1) 表头明确是其他表(变动原因/资产负债/股东/释义)
    for kw in ("变动比例", "变动原因", "变动情况", "主要原因",
               "期末余额", "期初余额", "股东", "前10名", "释义"):
        if kw in hdr:
            return True
    # 2) 表头无 CPI 特征(本报告期/年初至报告期末/项目/上年同期/报告期)，且数据行
    #    完全不含 CPI 字段关键词(如"其他综合收益/OCI 表"、无表头股东表) → 其他表。
    if not any(k in hdr for k in ("本报告期", "年初至报告期末", "上年同期", "报告期", "项目", "科目")):
        rows_txt = "".join(str(c or "") for row in (matrix.get("data_rows", []) or []) for c in row)
        if not any(k in rows_txt for k in ("每股收益", "净资产收益率", "营业收入", "营业总收入",
                                           "净利润", "总资产", "净资产", "经营活动")):
            return True
    return False


# 返工22 P3-1(最后一道兜底): 深交所"补充资料"式每股收益/ROE 表是"多行标签 + 多值列"
# 结构(加权平均净资产收益率|每股收益 分列)，matrix 提取后列错位、值列判不准。
# 实测云南白药2022FY p272: 加权平均净资产收益率=7.87% / 每股收益=1.90 就在页面上，
# 但 matrix 提取只拿到每股收益 1.90、丢了 roe_w。此处只在 CPI 缺失关键字段时，
# 从 section 页文本里用正则直接补抓这几项。

# 返工22 P3-10c: 排除 4 位年份(19xx/20xx)被当作数值捕获(实测 华润三九2024FY eps 曾匹配"2023")。
_NOT_YEAR = r"(?!(?:19|20)\d{2}(?!\.?\d))"

CPI_REGEX_FALLBACK = [
    # 返工22 P3-10a 补充资料表(年报/半年报末页): "加权平均净资产收益率"是【列头】，
    # 取值在"归属于公司普通股股东的净利润"行的第一列(非扣非口径, 与云南白药 anchor 一致)。
    # 实测 万邦德2023FY p256: 归属于…净利润 1.84% / 扣非 1.24% → 取 1.84%。
    ("roe_weighted_excl_non_recurring",
     re.compile(r"加权平均净资产收益率.*?"
                r"归属于公司普通股股东的净"
                r"(?:润)?[^%％]*?"
                + _NOT_YEAR + r"(-?\d+\.?\d*)\s*[%％]"),
     0.01),
    # 返工22 P3-10b 季度/半年报主要财务指标表: "加权平均净资产收益率（%）"后紧跟本/上年两列，
    # 中间夹"增加X个百分点"等说明文字，值前还带 eps/稀释列。取第一个百分比(本期)。
    # 强制要求括号+百分号（%）才认，避免误命中正文叙述"加权平均净资产收益率是7.87%"。
    ("roe_weighted_excl_non_recurring",
     re.compile(r"加权平均净资产收益率[（(][%％][）)][^%％]*?"
                + _NOT_YEAR + r"(-?\d+\.?\d*)\s*[%％]"),
     0.01),
    # 原有: 主要财务指标表"加权平均净资产收益率14.60%" / 正文叙述"…净资产收益率7.68%…"
    ("roe_weighted_excl_non_recurring",
     re.compile(r"加权平均净资产收益率[（(]?[%％]?[）)]?\s*"
                r"(?:(?:基本|稀释)每股收益(?:[（(]?元[/／]?股[）)]?)?\s*)*"
                r"(?:归属于公司普通股股东的净(?:利润)?\s*)?"
                + _NOT_YEAR + r"(-?\d+\.?\d*)\s*[%％]"),
     0.01),   # 百分比 → 归一化为小数(0.0787)，与主提取一致
    ("eps",
     re.compile(r"基本每股收益[（(]?[^（()]*?[）)]?\s*[:：]?\s*"
                + _NOT_YEAR + r"(-?\d+\.?\d*)\s*(?:元)?[/／]?股?"),
     1.0),
]


# 返工27: 兜底触发判断的"字段→中文标签关键词"映射。
# 原 missing 用英文字段名(roe_weighted_excl_non_recurring)查中文行文本，恒为真，兜底总是追加行。
# 改为按中文标签判断该字段是否已存在于矩阵，避免正则兜底与矩阵已有值竞争/覆盖。
_CPI_FIELD_LABEL_KW = {
    "roe_weighted_excl_non_recurring": ("加权平均净资产收益率",),
    "eps": ("基本每股收益",),
}


def _cpi_regex_fallback(pdf, pages, pdf_len, page_cache=None):
    """CPI 缺字段时，从候选页文本正则补抓 roe_w / eps。返回 {field: (value, label)}。

    返工22 P3-10: 先在 CPI section 候选页找；若 roe_w 仍缺，再按关键词"加权平均
    净资产收益率"扫全 PDF，把补充资料页(年报/半年报末页)纳入搜索。
    page_cache 复用主流程的页文本缓存，避免同一页被 extract_text 多次。
    """
    found = {}

    def flat_text(pi):
        if page_cache is not None:
            return page_cache.get(pi)
        try:
            return pdf.pages[pi].extract_text() or ""
        except IndexError:
            return ""

    def try_page(pi):
        flat = re.sub(r"\s+", "", flat_text(pi))
        if not flat:
            return
        for field, pat, scale in CPI_REGEX_FALLBACK:
            if field in found:
                continue
            m = pat.search(flat)
            if m:
                try:
                    val = float(m.group(1)) * scale
                except ValueError:
                    continue
                found[field] = (round(val, 6), m.group(0)[:24])
                if len(found) >= 2:
                    return True
        return False

    # 1) 候选页先搜（多数文件的主表/主要财务指标就在候选页里）
    for pi in pages:
        if try_page(pi):
            return found

    # 2) roe_w 仍缺时，全 PDF 扫关键词定位补充资料页（仅此情形才付出全扫代价）
    if "roe_weighted_excl_non_recurring" not in found:
        for pi in range(pdf_len):
            flat = re.sub(r"\s+", "", flat_text(pi))
            if "加权平均净资产收益率" in flat:
                if try_page(pi):
                    return found
    return found


def _is_parent_income_table(tbl_text):
    """识别母公司利润表(用于同页多表时排除)。

    合并利润表首行是"一、营业总收入"，母公司利润表首行是"一、营业收入"(无"总")。
    母公司表也含"净利润/利润总额"，若不用此特征排除，core-tail 关键词会误选母公司表，
    导致 net_profit 取到母公司口径、与合并 total_profit 异号(返工16 万邦德/粤万年青)。
    """
    return ("一、营业收入" in tbl_text) and ("营业总收入" not in tbl_text)



def extract_pages_for_type(pdf, table_type, section_pages, max_pages=50, page_cache=None):
    """从section索引中提取指定表类型的页面数据
    返回: 最佳matrix，或None
    """
    pages = section_pages.get(table_type, [])
    if not pages:
        return None

    profile = TABLE_PROFILES[table_type]
    end_markers = SECTION_END_MARKERS.get(table_type, [])
    best_matrix = None
    best_score = -1

    for start_page in pages:
        # 检查这页是否真的有该表的数据（不只是标题提及）
        page_text = pdf.pages[start_page].extract_text() or ""
        section_markers = TABLE_PROFILES[table_type].get("section_markers", [])
        has_own_marker = any(m in page_text for m in section_markers)
        content_check = score_page_for_type(page_text, table_type)
        if content_check[0] < 40 and not has_own_marker:
            # 返工16修1: 标题页可能只有标题没有数据行(数据在后续页)。
            # 只要有目标表标题(has_own_marker)就不跳过，否则像 600129 的84页
            # 只有"合并资产负债表"标题 content=16 被跳过 → 分块失败 → 回退漂移到母公司表。
            continue

        # 如果起始页就有其他section的标记，说明多个section在同一页
        has_end_marker_on_start = any(m in page_text for m in end_markers)

        if has_end_marker_on_start and not has_own_marker:
            # 本section的标记不在这里，跳过
            continue

        # 从start_page开始，连续扫描直到遇到下一个section的边界
        scan_range = [start_page]
        if has_end_marker_on_start:
            # 起始页已有其他section标记，只用这一页（不向前扩展）
            pass
        else:
            # 返工22修1: CPI 是 1~2 页的小表，向后只扩 1 页；三表维持原有 5 页不变
            span = CPI_MAX_SPAN if table_type == CPI_TT else 5
            end_pi = start_page + span if max_pages is None else min(start_page + span, max_pages)
            for pi in range(start_page + 1, end_pi):
                try:
                    text = pdf.pages[pi].extract_text() or ""
                except IndexError:
                    break
                # 返工22修1: CPI 续页边界。
                # 页首即隔壁表(变动原因/股东/非经常性损益/释义) → 不纳入；
                # 页中才出现 → 本页上半仍是 CPI 续表，纳入本页后停止(同母公司表的处理)。
                cpi_stop_in_page = False
                if table_type == CPI_TT:
                    cpi_head = "\n".join(text.splitlines()[:6])
                    if any(k in cpi_head for k in CPI_STOP_KW):
                        break
                    cpi_stop_in_page = any(k in text for k in CPI_STOP_KW)
                # 返工11: 只在页面前4行识别通用章节标题，避免续页中下部的"母公司XX"误触发
                head = "\n".join(text.splitlines()[:4])
                if any(m in head for m in end_markers):
                    break
                # 返工15修3+修5: 母公司表标题是精确章节词。
                # 标题在页首 → 纯母公司表页，直接停止不纳入。
                # 标题在页中/页尾 → 本页上方是合并表尾部(如"四、利润总额")，纳入本页后停止。
                parent_kws = PARENT_TITLE_KW.get(table_type, [])
                parent_in_head = any(kw in head for kw in parent_kws)
                parent_in_page = any(kw in text for kw in parent_kws)
                if parent_in_head:
                    break
                scan_range.append(pi)
                if cpi_stop_in_page or parent_in_page:
                    break

        # 在扫描范围内提取表格（跨页合并）
        page_matrices = []
        for pi in scan_range:
            matrix = extract_matrix_from_page(pdf, pi, table_type=table_type)
            if not matrix or len(matrix.get('data_rows', [])) < 2:
                matrix = extract_borderless_table(pdf, pi)
            if matrix and len(matrix.get('data_rows', [])) >= 2:
                # 返工25: CPI 在源头跳过明确的其他表(变动原因/资产负债/股东/OCI/释义)，
                # 防止其高得分胜出(同仁堂2023Q3)或污染跨页合并(济川2023Q3)。
                if table_type == CPI_TT and _is_cpi_foreign_table(matrix):
                    continue
                score = score_matrix_content(matrix, profile)
                page_matrices.append((pi, matrix, score))

        if not page_matrices:
            continue

        # 返工22修2: CPI 跨页合并前的"值列一致性闸"。合并前按"目标值列位置"过滤，
        # 防止"本报告期列"与"年初至报告期末列"位置不同的表被错误拼合。
        # 返工25: 保留表头页(含"本报告期/年初至报告期末")，即使其值列与锚页不同——
        # CPI 流量页(累计列)与比率/时点页(本报告期列)值列天然不同，但流量页表头是干净的，
        # 旧闸误删流量页会导致表头继承丢失、续页首行被提升成坏表头(济川2025Q3)。
        if table_type == CPI_TT and len(page_matrices) > 1:
            anchor_pi, anchor_m, _ = max(page_matrices, key=lambda x: x[2])
            anchor_col = _value_col_index(anchor_m)
            if anchor_col is not None:
                compat = []
                for pi, m, s in page_matrices:
                    vc = _value_col_index(m)
                    hdr_txt = "".join(str(c or "") for c in (m.get("header_row", []) or []))
                    is_hdr_page = ("本报告期" in hdr_txt) or ("年初至报告期末" in hdr_txt)
                    if vc is None or vc == anchor_col or is_hdr_page:
                        compat.append((pi, m, s))
                    else:
                        print("  [CPI列闸] p%d 值列=%s 与最佳页 p%d 值列=%s 不一致 → 不合并" % (pi, vc, anchor_pi, anchor_col))
                if compat:
                    page_matrices = compat

        # 如果只有一页，直接取最好的
        if len(page_matrices) == 1:
            pi, matrix, score = page_matrices[0]
            if score > best_score:
                best_score = score
                best_matrix = preprocess_table(matrix)
                best_matrix['_page'] = pi
        else:
            # 跨页合并：取header_row来自最高分的页，data_rows合并所有页
            # (母公司行由 SECTION_END_MARKERS 的母公司边界阻止进入 scan_range)
            page_matrices.sort(key=lambda x: -x[2])
            best_pi, best_m, best_s = page_matrices[0]
            # 返工12: merged_header 取"含表头关键词"且页序最早的页；无则取分数最高页
            # 防止续页（无表头、第一行是数据）被当表头 → analyze 把 target 定到附注列 → 整表丢失
            HEADER_KW = ["项目","附注","期末余额","期初余额","本期金额","上期金额",
                         "本报告期","上年同期","报告期","金额"]
            header_cands = [(pi, m) for pi, m, s in page_matrices
                            if any(k in "".join(str(c or "") for c in m.get("header_row", [])) for k in HEADER_KW)]
            if header_cands:
                src_pi, src_m = min(header_cands, key=lambda x: x[0])
            else:
                src_pi, src_m, _ = min(page_matrices, key=lambda x: x[0])
            merged_header = src_m.get('header_row', [])
            merged_rows = []
            seen_labels = set()
            # 返工16修1: 跨页合并必须按页码升序,不能按分数降序(否则资产段被排到负债段后面)
            for pi, m, s in sorted(page_matrices, key=lambda x: x[0]):
                for row in m.get('data_rows', []):
                    label = str(row[0] or "").strip()
                    if label and label not in seen_labels:
                        merged_rows.append(row)
                        seen_labels.add(label)
                    elif not label:
                        merged_rows.append(row)
            if merged_rows:
                merged_matrix = {'header_row': merged_header, 'data_rows': merged_rows}
                merged_score = score_matrix_content(merged_matrix, profile)
                if merged_score > best_score:
                    best_score = merged_score
                    best_matrix = preprocess_table(merged_matrix)
                    best_matrix['_page'] = best_pi

    # 返工22修3: CPI 标签清洗（仅 CPI 路径，三表标签不碰）
    if table_type == CPI_TT and best_matrix:
        best_matrix = _clean_cpi_matrix_labels(best_matrix)

    # 返工22 P3-1 兜底: 仅 CPI 缺关键字段(加权ROE/每股收益)时，从候选页文本正则补抓。
    if table_type == CPI_TT:
        all_rows_txt = ""
        if best_matrix:
            for row in best_matrix.get("data_rows", []):
                all_rows_txt += " ".join(str(c or "") for c in row) + " "
        # 返工27: 原 missing 用英文字段名查中文行文本恒真，兜底总是追加行；改为按中文标签判断字段是否真正缺失。
        missing = []
        for f, _p, _s in CPI_REGEX_FALLBACK:
            kws = _CPI_FIELD_LABEL_KW.get(f, ())
            if not kws or not any(kw in all_rows_txt for kw in kws):
                missing.append(f)
        if missing:
            extra = _cpi_regex_fallback(pdf, pages, len(pdf.pages), page_cache)
            if extra:
                # 补充资料式表：正则补抓的字段作为额外数据行，交由主流程映射。
                if best_matrix is None:
                    best_matrix = {"header_row": [], "data_rows": [], "source": "cpi_regex"}
                best_matrix = dict(best_matrix)
                new_rows = list(best_matrix.get("data_rows", []))
                missing_set = set(missing)
                for f, (val, label) in extra.items():
                    if f in missing_set:  # 返工27: 只追加真正缺失的字段，不覆盖矩阵已有值
                        new_rows.append([label, str(val)])
                        print("  [CPI正则兜底] %s = %s (←%s)" % (f, val, label))
                best_matrix["data_rows"] = new_rows
                best_matrix["source"] = best_matrix.get("source", "cpi_regex")

    return best_matrix


def score_matrix_content(matrix, profile):
    """对已提取的matrix评分（基于内容匹配，不看页面位置）

    返工22修4: 只有"非叙述性单元格"里的关键词才计分。
    实测云南白药2022FY: p16「四、主营业务分析」的叙述性正文被当表提取，
    正文里"…7.00%；加权平均净资产收益率是7.…分点；基本每股收益 1.90元/股。"
    关键词堆砌得155分，碾压真表 p6(100)/p272(118) → 选中一页散文 → CPI 全空(no_cpi)。

    判据用"单元格是否像句子"(含句读标点 或 超长)，而不是"行内有无纯数值"——
    实测无框线表(borderless)的真表页结构化行占比也可能低到 0.06(云南白药 p120 利润表)，
    用占比会误伤三表。财务报表的科目标签不含句读标点，叙述正文必然含。
    """
    if not matrix or not matrix.get('data_rows'):
        return 0

    label_text = ""   # 非叙述单元格(科目标签/表头/数值)
    for row in [matrix.get('header_row', [])] + matrix['data_rows']:
        for c in row:
            s = str(c or "").strip()
            if s and not _cell_is_prose(s):
                label_text += s + " "

    score = 0
    for kw, pts in profile.get('weight_kw', {}).items():
        if kw in label_text:
            score += pts
    return score


def score_page_for_type(page_text, table_type, title_context=None):
    """对单页文本，按目标表类型评分子维度得分（无800字符限制）"""
    profile = TABLE_PROFILES[table_type]
    text_all = page_text
    scores = {}

    # 标题匹配（全文扫描，不再限制800字符）
    hs = 0
    for kw in profile["section"]:
        if kw in text_all: hs += 15
    scores["header"] = min(hs, 30)

    # M2: 标题继承得分
    ts = 0
    if title_context:
        for poff, tline in title_context:
            wt = max(15 - poff * 5, 5)
            if any(k in tline for k in profile["section"]):
                ts += wt
            elif any(k in tline for k in ["主要","财务数据","财务指标","会计数据"]):
                ts += wt // 2
    scores["title_inheritance"] = min(ts, 15)

    # 表格结构（最重要 - 必须先算，用于调节其他分数）
    tbl_lines = [l for l in text_all.split("\n") if len(l.strip())>10 and re.search(r"\d",l)]
    tbl_count = len(tbl_lines)
    scores["table_structure"] = min(tbl_count, 20)

    # 关键词密度 - 如果没有足够表格结构，大幅降低关键词得分
    # 叙述文字中也可能出现关键词，需要表格行支撑
    kw_score = 0
    for kw, pts in profile["weight_kw"].items():
        cnt = text_all.count(kw)
        if cnt > 0: kw_score += pts * min(cnt, 3)
    raw_kw = min(max(kw_score // 5, 0), 25)
    if tbl_count < 10:
        # 表格行不够，关键词可能只是叙述文字中的提及
        raw_kw = raw_kw * tbl_count // 10
    scores["subject_density"] = raw_kw

    # 数值密度 - 同样需要表格结构支撑
    nums = re.findall(r"[+-]?(?:\d[\d,]*\.?\d*)(?:万|亿|元)?", text_all)
    big = [n for n in nums if len(n.replace(",","").replace(".",""))>=5 or "亿" in n or "万" in n]
    raw_num = min(len(big)*3, 25)
    if tbl_count < 10:
        raw_num = raw_num * tbl_count // 10
    scores["number_density"] = raw_num

    total = sum(scores.values())
    return total, scores


def detect_fuzhu_column(header_row):
    """检测表头是否含附注列，返回目标数值列索引"""
    if not header_row: return 1
    for i,h in enumerate(header_row):
        if chr(38468) in h:
            for j in range(i+1,len(header_row)):
                if header_row[j].strip(): return j
            return i+1
    return 1


def _is_amount_text(s):
    """返工17修3: 判断单元格是否为金额数字(区别于标签文本/年份/注编号)。
    口径同 _row_has_amount: 带小数点、千分位逗号、或≥5位整数。"""
    s = str(s).strip()
    if not s:
        return False
    if s.endswith("%") or s.endswith("％"):  # 增减/百分比列(如"-6.39%")不是金额
        return False
    if re.search(r'\d{4}\s*年', s):  # 年份/表头词
        return False
    if re.search(r'-?[\d,]+\.\d{2}', s):  # 带小数点金额
        return True
    if re.search(r'-?\d[\d,]*,[\d,]{3,}', s):  # 千分位整数
        return True
    if re.search(r'-?\d{5,}', s):  # ≥5位整数
        return True
    return False


def _align_value_columns(header_row, data_rows):
    """返工17修3: 按页对齐值列。

    跨页合并的根因修复: 利润表续页(如云南白药 p128)因"附注"列/合并单元格被
    pdfplumber 拆出额外列,数值被推到第3/4列(中间夹标签溢出列+空列)。旧逻辑把
    filter_sparse_columns 放到跨页合并之后统一执行,续页该列为空 → 首页的营收/成本
    值列被当稀疏列删除,导致"一、营业总收入"值整体丢失(000538 营收=1548万错取营业外收入)。
    此处按页用"金额密度"识别真正的值列,统一成 [标签, 值列...],合并才不会串列。
    """
    if not data_rows:
        return header_row, data_rows
    n_cols = max(len(r) for r in data_rows)
    if n_cols <= 2:
        return header_row, data_rows
    # 值列 = 非第0列中, 金额数字占比高的列
    value_cols = []
    for ci in range(1, n_cols):
        amount = nonempty = 0
        for r in data_rows:
            if ci >= len(r):
                continue
            s = str(r[ci]).strip()
            if not s:
                continue
            nonempty += 1
            if _is_amount_text(s):
                amount += 1
        if amount >= 3 and amount / max(nonempty, 1) >= 0.6:
            value_cols.append(ci)
    if len(value_cols) <= 1:
        return header_row, data_rows
    # 投影: 标签 = 第0列 + 其余非值列的文本(标签溢出); 值 = 各值列
    from pdf_extractor.value_normalizer import parse_number
    new_rows = []
    for r in data_rows:
        label = str(r[0]).strip() if len(r) > 0 else ""
        for ci in range(1, n_cols):
            if ci in value_cols:
                continue
            if ci < len(r):
                s = str(r[ci]).strip()
                # 只并入非纯数字的标签文本(避免把附注编号"1/2/3"并进标签)
                if s and parse_number(s) is None:
                    label += s
        vals = [str(r[ci]).strip() if ci < len(r) else "" for ci in value_cols]
        new_rows.append([label] + vals)
    if header_row:
        # 返工17修3(修): 表头值列不能按"数据值列下标"取——表头与数据值列常错位一列
        # (如万邦德 项目在第1列、年度在第4/7列，而数据值在第3/6列)。改用表头自身结构:
        # 标签列 = 含"项目/科目/附注"的列; 值列 = 其余非空、非金额的单元格(年度/本期/上期)按左右顺序。
        label_hdr = ""
        for c in header_row:
            s = str(c).strip()
            if s and any(k in s for k in ("项目", "科目", "附注")):
                label_hdr = s
                break
        if not label_hdr:
            label_hdr = str(header_row[0]).strip() if len(header_row) > 0 else ""
        val_hdrs = []
        for c in header_row:
            s = str(c).strip()
            if not s or s == label_hdr:
                continue
            if any(k in s for k in ("项目", "科目", "附注")):
                continue
            if _is_amount_text(s):
                continue
            val_hdrs.append(s)
        # 与数据值列数对齐(不足补空, 超出截断)
        val_hdrs = val_hdrs[:len(value_cols)]
        while len(val_hdrs) < len(value_cols):
            val_hdrs.append("")
        new_header = [label_hdr] + val_hdrs
    else:
        new_header = header_row
    return new_header, new_rows


def extract_matrix_from_page(pdf, page_idx, table_type=None):
    """从指定页提取表格矩阵"""
    try: page = pdf.pages[page_idx]
    except IndexError: return None
    tables = page.extract_tables()
    if not tables: return None

    type_kw = set()
    if table_type:
        profile = TABLE_PROFILES.get(table_type, {})
        type_kw = set(profile.get("weight_kw", {}).keys()) if profile else set()
    header_kw = ["项目","附注","期末余额","期初余额","本期金额","上期金额","本报告期","上年同期","报告期","金额"]

    scored_tables = []
    for tbl in tables:
        if not tbl or len(tbl) < 2: continue
        tbl_text = " ".join(" ".join(str(c or "") for c in r) for r in tbl)
        content_score = 0
        if type_kw:
            matches = sum(1 for kw in type_kw if kw in tbl_text)
            content_score = min(matches * 8, 60)
        header_score = 0
        for i in range(min(3, len(tbl))):
            rt = "".join(str(c or "") for c in tbl[i])
            if any(kw in rt for kw in header_kw):
                header_score = 25
                break
        total = content_score + header_score
        scored_tables.append((total, content_score, len(tbl), tbl, tbl_text))

    if not scored_tables: return None

    # 返工16修1: 同页多表时,优先选含"核心尾部行"的表(合并表尾部),防止带"项目"表头的
    # 母公司表抢占无表头的合并表续表。例: 600080 p34 同页含 合并表尾部(负债合计+所有者权益)
    # 和 母公司表(流动资产),旧逻辑给母公司表 header+25 导致选错、权益段整段丢失。
    if table_type and len(scored_tables) > 1:
        core_kws = TABLE_CORE_TAIL_KW.get(table_type, [])
        if core_kws:
            # 返工16修5: 先排除母公司表。母公司利润表也含"净利润/利润总额"，
            # 若不排除会被 core-tail 关键词误选，导致 net_profit 取母公司口径、与合并 total_profit 异号。
            if table_type == "income_sheet":
                non_parent = [s for s in scored_tables if not _is_parent_income_table(s[4])]
                if non_parent:
                    scored_tables = non_parent
            core_scored = [s for s in scored_tables if any(kw in s[4] for kw in core_kws)]
            if core_scored:
                scored_tables = core_scored

    scored_tables.sort(key=lambda x: (x[1], x[2]), reverse=True)
    best = scored_tables[0][3]
    if not best or len(best) < 2: return None

    header_row, header_idx = None, 0
    for i in range(min(3, len(best))):
        rt = "".join(str(c or "") for c in best[i])
        if any(kw in rt for kw in header_kw):
            header_row = [str(c or "").strip() for c in best[i]]
            header_idx = i
            break
    if not header_row:
        # 返工15修4: 续表页首行是数据行(如"四、利润总额 911,550,395.40")，不能当表头丢弃。
        # header_row 置空、data_rows 从第0行开始，表头由 preprocess_table 的动态表头逻辑重判。
        header_row = []
        header_idx = -1

    data_rows = []
    for row in best[header_idx+1:]:
        cl = [str(c or "").strip() for c in row]
        if any(c for c in cl if len(c) > 0):
            data_rows.append(cl)

    # 返工17修3: 按页对齐值列成 [标签, 值列...]，统一跨页各页列结构后再合并。
    # 有表头的页也要对齐：表头按自身结构重投影(标签列="项目/科目/附注", 值列=其余非空年度词)，
    # 而非用数据值列下标，避免"项目/年度"标签被打空(万邦德 项目在第1列、年度在第4/7列)。
    header_row, data_rows = _align_value_columns(header_row, data_rows)
    return {"header_row": header_row, "data_rows": data_rows}


def get_title_context(pdf, page_idx, n_above=3):
    """向上取最近的标题行作为上下文 (M2)"""
    titles = []
    for offset in range(1, min(n_above, page_idx) + 1):
        try:
            text = pdf.pages[page_idx - offset].extract_text() or ''
            for line in text.split(chr(10)):
                line = line.strip()
                if not line: continue
                if any(kw in line for kw in [chr(12289), chr(20027), chr(35201), chr(21033), chr(36164), chr(29616), chr(36130), chr(25454), chr(25351), chr(35745), chr(20250)]):
                    titles.append((offset, line))
                    break
        except IndexError: break
    return titles


def classify_page_type(page_idx, page_text):
    """判断页类型: summary(摘要) | statement(完整报表)"""
    if page_idx == 0:
        sc = sum(1 for kw in SUMMARY_KW if kw in page_text)
        has_section = any(s in page_text[:600] for s in ["主要会计数据","利润表","资产负债表"])
        if sc >= 3 and not has_section: return "summary"
    return "statement"


# 事项3: 行级过滤 — 剔除跨表污染行
_CF_POLLUTION_KW = ["产生的现金流量净额", "现金流量", "现金及现金等价物"]

def _filter_cross_table_rows(matrix, table_type):
    """过滤跨表污染：利润表不应含现金流量字段"""
    if table_type != "income_sheet":
        return matrix
    if not matrix or not matrix.get("data_rows"):
        return matrix
    rows = matrix["data_rows"]
    filtered = [r for r in rows if not any(kw in str(r[0]) for kw in _CF_POLLUTION_KW)]
    removed = len(rows) - len(filtered)
    if removed > 0:
        matrix = dict(matrix)  # 浅拷贝，避免修改原对象
        matrix["data_rows"] = filtered
        print(f"  [行过滤] income_sheet 剔除 {removed} 行现金流字段")
    return matrix


def _find_best_value_column(matrix_data):
    """自动找本期金额列（跳过标签列0），返回列下标和该列的所有值

    事二修复v2: 优先用 column_recognizer.detect_column_type 判断 target/prev，
    只有无表头信息时才退回纯数字计数。
    """
    from pdf_extractor.value_normalizer import parse_number
    from pdf_extractor.column_recognizer import detect_column_type
    rows = matrix_data.get('data_rows', [])
    header = matrix_data.get('header_row', [])
    if not rows:
        return 1, []
    max_cols = max(len(r) for r in rows)

    # 用 column_recognizer 判断每列类型
    col_types = {}
    for ci in range(1, min(max_cols, 6)):
        hdr = str(header[ci]) if ci < len(header) else ''
        ct = detect_column_type(hdr)
        col_types[ci] = ct

    best_col, best_score = 1, 0
    for ci in range(1, min(max_cols, 6)):
        ct = col_types.get(ci, 'other')
        # prev/delta 列 → 直接跳过
        if ct in ('prev', 'delta'):
            continue
        # 数字计数
        num_count = sum(1 for r in rows if len(r) > ci and parse_number(r[ci]) is not None)
        # target 列 → ×3 优先
        if ct in ('target', 'target_cumulative'):
            num_count *= 3

        if num_count > best_score:
            best_score = num_count
            best_col = ci

    raw_vals = [r[best_col] for r in rows if len(r) > best_col]
    return best_col, raw_vals


def _scan_global_unit(page_cache, max_pages=None):
    """扫描整个PDF，找到所有单位声明，返回最常见的单位倍率
    改进：全页扫描（不再限制50页），使用页缓存避免重复提取
    返回: (factor, found) — found=True 表示至少找到一处明确声明
    """
    from pdf_extractor.value_normalizer import detect_unit
    unit_counts = {}
    n = page_cache.total_pages() if max_pages is None else min(page_cache.total_pages(), max_pages)
    for i in range(n):
        text = page_cache.get(i)
        uf, found = detect_unit(text)
        if found:  # 找到了明确的单位声明就统计，不管是不是元
            unit_counts[uf] = unit_counts.get(uf, 0) + 1
    if not unit_counts:
        return 1, False
    # 返回出现最多的单位倍率
    return max(unit_counts, key=unit_counts.get), True


def extract_all_tables(pdf_path, max_pages=None):
    """主提取函数: 分块优先，评分回退"""
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception:
        return {}

    total_page_count = len(pdf.pages)
    n_pages = total_page_count if max_pages is None else min(total_page_count, max_pages)

    # ===== 页文本缓存：避免同一页重复 extract_text() =====
    page_cache = _PageTextCache(pdf)

    # ===== 第零步：全局单位扫描（全页，不限50页） =====
    global_unit_factor, global_unit_found = _scan_global_unit(page_cache, max_pages=None)

    # ===== 第一步：M1语义分块定位 =====
    from pdf_extractor.pdf_segmenter import get_section_pages
    section_pages, variant_pages, blocks = get_section_pages(pdf_path, max_pages)

    # ===== 第二步：分块提取 =====
    result = {}
    n_pages_total = n_pages

    # 事项2修正: 全文扫描三表标题 — 摘要PDF(2-3页)无"利润表/资产负债表/现金流量表"标题
    total_pages = page_cache.total_pages()
    has_income_title = any("利润表" in page_cache.get(i) for i in range(total_pages))
    has_balance_title = any("资产负债表" in page_cache.get(i) for i in range(total_pages))
    has_cashflow_title = any("现金流量表" in page_cache.get(i) for i in range(total_pages))
    is_summary = not (has_income_title or has_balance_title or has_cashflow_title)
    SUMMARY_SKIP_TABLES = {"income_sheet", "balance_sheet", "cash_flow_sheet"}
    if is_summary:
        print("  [摘要版] 全文无三表标题，跳过主表仅提取核心指标")

    from pdf_extractor.value_normalizer import detect_unit_above, find_best_unit
    for tt in TABLE_PROFILES:
        # 摘要版PDF跳过三张主表，只保留CPI
        if is_summary and tt in SUMMARY_SKIP_TABLES:
            continue

        # P0-2: 合并表优先 — 同时有合并和母公司表时，用合并表页码
        vp = variant_pages.get(tt, {})
        extract_pages = section_pages.get(tt, [])
        sheet_variant = 'unknown'
        if vp.get('consolidated'):
            extract_pages = vp['consolidated']
            sheet_variant = 'consolidated'
        elif vp.get('parent'):
            extract_pages = vp['parent']
            sheet_variant = 'parent'

        # 用选定的页码构造临时 section_pages 给 extract_pages_for_type
        temp_section = {tt: extract_pages}
        matrix_data = extract_pages_for_type(pdf, tt, temp_section, max_pages, page_cache)
        if matrix_data:
            matrix_data = _filter_cross_table_rows(matrix_data, tt)
            page_idx = matrix_data.get('_page', section_pages.get(tt, [0])[0])
            # 检测单位（上下都找，扩展搜索范围）
            above_texts = []
            for pi in range(page_idx-1, max(page_idx-6,-1), -1):
                if pi < 0: break
                pt = page_cache.get(pi)
                if pt.strip(): above_texts.append((pt, pi))
            below_texts = []
            for pi in range(page_idx+1, min(page_idx+4, n_pages_total)):
                pt = page_cache.get(pi)
                if pt.strip(): below_texts.append((pt, pi))
            page_text = page_cache.get(page_idx)
            unit_factor, unit_raw, unit_source_text = detect_unit_above(page_text, above_texts, below_texts, global_unit_factor, global_unit_found)

            # 量级校验：用表格实际金额中位数反算确认单位是否合理
            _, raw_vals = _find_best_value_column(matrix_data)
            unit_factor, unit_raw, unit_source_text = find_best_unit(
                raw_vals, unit_factor, global_unit_factor, global_unit_found
            )

            result[tt] = {
                "score": 100,  # 分块提取的置信度设为100
                "page": page_idx,
                "page_text": page_text,
                "matrix": matrix_data,
                "unit_factor": unit_factor,
                "unit_raw": unit_raw,
                "unit_source_text": unit_source_text,
                "scores_breakdown": {"method": "section_based"},
                "source_type": "statement",
                "sheet_variant": sheet_variant,  # P0-2
            }

    # ===== 第三步：评分回退（分块没找到的表） =====
    missing = [tt for tt in TABLE_PROFILES if tt not in result and not (is_summary and tt in SUMMARY_SKIP_TABLES)]
    if missing:
        best = {}
        for tt in missing:
            best[tt] = {"score":0,"page":-1,"page_text":"","matrix":None,"unit_factor":1,"unit_raw":"元","unit_source_text":"","scores_breakdown":{},"source_type":"statement"}

        for page_idx in range(n_pages):
            page_text = page_cache.get(page_idx)
            if not page_text.strip(): continue

            source_type = classify_page_type(page_idx, page_text)
            still_searching = False

            for tt in missing:
                bp = best[tt]
                if bp["score"] >= TABLE_PROFILES[tt]["exit_threshold"]: continue
                still_searching = True

                titles = get_title_context(pdf, page_idx) if page_idx > 0 else []
                total, breakdown = score_page_for_type(page_text, tt, title_context=titles)
                if total > bp["score"]:
                    bp["score"] = total; bp["page"] = page_idx
                    bp["page_text"] = page_text; bp["scores_breakdown"] = breakdown
                    bp["source_type"] = source_type

                    matrix = extract_matrix_from_page(pdf, page_idx, table_type=tt)
                    if not matrix or len(matrix.get('data_rows',[])) < 2:
                        matrix = extract_borderless_table(pdf, page_idx)
                    if matrix and len(matrix.get('data_rows',[])) >= 2:
                        matrix = _filter_cross_table_rows(matrix, tt)
                        bp["matrix"] = preprocess_table(matrix)
                        above_texts = []
                        for pi in range(page_idx-1, max(page_idx-6,-1), -1):
                            if pi < 0: break
                            pt = page_cache.get(pi)
                            if pt.strip(): above_texts.append((pt, pi))
                        below_texts = []
                        for pi in range(page_idx+1, min(page_idx+4, n_pages)):
                            pt = page_cache.get(pi)
                            if pt.strip(): below_texts.append((pt, pi))
                        unit_factor, unit_raw, unit_source_text = detect_unit_above(page_text, above_texts, below_texts, global_unit_factor, global_unit_found)
                        # 量级校验
                        _, raw_vals = _find_best_value_column(matrix)
                        unit_factor, unit_raw, unit_source_text = find_best_unit(
                            raw_vals, unit_factor, global_unit_factor, global_unit_found
                        )
                        bp["unit_factor"] = unit_factor
                        bp["unit_raw"] = unit_raw
                        bp["unit_source_text"] = unit_source_text

            if not still_searching: break

        # 合并回退结果
        for tt in missing:
            bp = best[tt]
            # P0-2: 回退路径也标记 sheet_variant
            bp.setdefault("sheet_variant", sheet_variant if 'sheet_variant' in dir() else 'unknown')
            if bp["page"]>=0 and bp["score"]>=TABLE_PROFILES[tt]["threshold"]:
                result[tt] = bp
            elif bp["page"]>=0:
                para_data = extract_from_paragraphs(pdf, bp["page"], table_type=tt)
                if para_data:
                    bp["para_data"] = para_data
                    bp["source_type"] = "paragraph"
                    result[tt] = bp

    pdf.close()
    return result


def extract(pdf_path, max_pages=50):
    """旧接口兼容: 取最高分表"""
    all_t = extract_all_tables(pdf_path, max_pages)
    if not all_t: return None
    best_tt = max(all_t, key=lambda k: all_t[k]["score"])
    return all_t[best_tt]


def extract_borderless_table(pdf, page_idx):
    """无框线表格: 用文字坐标重建二维矩阵"""
    try:
        page = pdf.pages[page_idx]
    except IndexError:
        return None
    words = page.extract_words(keep_blank_chars=True, x_tolerance=3)
    if not words or len(words) < 6:
        return None
    rows = {}
    for w in words:
        y_key = round(w["top"] / 3) * 3
        rows.setdefault(y_key, []).append(w)
    sorted_y = sorted(rows.keys())
    if len(sorted_y) < 3:
        return None
    all_x = []
    for w in words:
        all_x.append(w["x0"]); all_x.append(w["x1"])
    all_x.sort()
    x_clusters = [[all_x[0]]]
    for x in all_x[1:]:
        if x - x_clusters[-1][-1] > 15:
            x_clusters.append([x])
        else:
            x_clusters[-1].append(x)
    col_bounds = [(min(c), max(c)) for c in x_clusters if len(c) >= 2]
    if len(col_bounds) < 2:
        return None
    matrix = []
    for y_key in sorted_y:
        row_words = rows[y_key]
        row = [""] * len(col_bounds)
        for w in row_words:
            cx = (w["x0"] + w["x1"]) / 2
            for ci, (x_min, x_max) in enumerate(col_bounds):
                if x_min <= cx <= x_max:
                    row[ci] = (row[ci] + " " + w["text"]).strip()
                    break
        if any(cell for cell in row if cell.strip()):
            matrix.append(row)
    if len(matrix) < 3:
        return None
    header_row = None
    header_idx = 0
    for i in range(min(3, len(matrix))):
        rt = "".join(matrix[i])
        if any(kw in rt for kw in [chr(39033), chr(38468), chr(26399), chr(26412)]):
            header_row = matrix[i]; header_idx = i; break
    if not header_row:
        # 返工15修4: 续表页首行是数据行，不能当表头丢弃。
        header_row = []
        header_idx = -1
    data_rows = matrix[header_idx+1:]
    return {"header_row": header_row, "data_rows": data_rows, "source": "borderless"}


def filter_sparse_columns(rows, threshold=0.30):
    if not rows:
        return rows
    n_cols = max(len(r) for r in rows)
    keep_cols = []
    for ci in range(n_cols):
        non_empty = 0
        for row in rows:
            if ci < len(row) and row[ci] and str(row[ci]).strip():
                non_empty += 1
        ratio = non_empty / len(rows)
        if ratio >= threshold:
            keep_cols.append(ci)
    if len(keep_cols) == n_cols:
        return rows
    result = []
    for row in rows:
        new_row = []
        for ci in keep_cols:
            new_row.append(row[ci] if ci < len(row) else "")
        result.append(new_row)
    if len(keep_cols) <= 1:
        return rows
    return result


def _row_has_amount(row):
    """返工15修1: 判断一行(非首列)是否含金额数字。
    金额 = 带小数点数字(2,474,338,981.11) 或 整数金额(1,042,431,410)。
    排除表头年份/单位词(2024年半年度)。"""
    for cell in row[1:]:
        s = str(cell).strip()
        if not s:
            continue
        # 表头年份/单位词: "2024年半年度"、"2024年1-6月" → 不是金额
        if re.search(r'\d{4}\s*年', s):
            continue
        # 强金额: 带小数点的数字
        if re.search(r'-?[\d,]+\.\d{2}', s):
            return True
        # 整数金额兜底: 千分位逗号 或 ≥5位数字(排除4位纯年份)
        if re.search(r'-?\d[\d,]*,[\d,]{3,}', s):
            return True
        if re.search(r'-?\d{5,}', s):
            return True
    return False


def merge_multirow_header(rows, header_rows=None):
    """返工15修1: 动态判定表头行数。扫描前3行，第一个含金额的行=数据首行，其前=表头。
    支持1/2/3行表头；前3行都无金额时按单行表头兜底。"""
    if not rows:
        return [], []
    if header_rows is None:
        header_rows = 1  # 兜底单行表头
        for i in range(min(3, len(rows))):
            if _row_has_amount(rows[i]):
                header_rows = i  # 数据首行在第i行，表头占i行
                break
    if header_rows <= 0:
        # 第0行就含金额(无表头)，全部作为数据
        return [], rows
    if len(rows) <= header_rows:
        return (rows[0] if rows else [], rows[1:] if len(rows) > 1 else [])
    headers = rows[:header_rows]
    data = rows[header_rows:]
    n_cols = max(len(r) for r in headers)
    merged = []
    for ci in range(n_cols):
        parts = []
        for row in headers:
            cell = str(row[ci]).strip() if ci < len(row) else ""
            if cell:
                parts.append(cell)
        merged.append("".join(parts) if parts else "")
    return merged, data


def merge_split_rows(data_rows):
    """返工15修2 + 返工16修2: 只拼"标签被PDF断行"的行(纯前缀引导词)，不拼空科目行和分节标题。

    返工16: 旧逻辑把"空科目行"(永续债/专项储备/一般风险准备/少数股东权益 等空值科目)
    和"分节标题"(流动资产:/所有者权益（或股东权益）:) 拼到下一行有值科目，
    导致"所有者权益合计"被污染成"少数股东权益所有者权益合计"、equity 误取"实收资本"值。
    """
    result = []
    i = 0
    # 新科目序号前缀: 三、 / （一） / （1） / 1. / 1、
    NEW_SECTION_RE = re.compile(
        r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|（\d+）|\d+[\.、])"
    )
    # 纯前缀引导词(独立成行、无值、后面才是真正的科目名) — 只有这些才需要拼续文
    CONTINUATION_PREFIXES = {
        "其中", "其中：", "其中:", "减", "减：", "减:", "加", "加：", "加:", "其", "其：", "其:",
    }

    while i < len(data_rows):
        row = data_rows[i]
        label = str(row[0]).strip() if row else ""
        values = row[1:] if len(row) > 1 else []
        all_empty = True
        has_label = bool(label)
        for v in values:
            vs = str(v).strip()
            if vs and vs not in ("", "-", "--", chr(8212)):
                all_empty = False
                break
        if has_label and all_empty and i + 1 < len(data_rows):
            next_row = data_rows[i + 1]
            next_label = str(next_row[0]).strip() if next_row else ""
            next_values = next_row[1:] if len(next_row) > 1 else []
            # 修2: 下一行必须有数值(两个空行不该拼)
            next_all_empty = all(str(v).strip() in ("", "-", "--", chr(8212)) for v in next_values)
            # 修2: 下一行不是新科目开头(序号前缀)
            not_new_section = not NEW_SECTION_RE.match(next_label)
            # 返工16修2: 分节标题(冒号结尾)是段落标题,不是标签断行,不拼
            is_section_heading = label.endswith(("：", ":"))
            # 返工16修2: 只有纯前缀引导词才代表"标签断行";普通空科目是独立行,不拼
            is_continuation = label in CONTINUATION_PREFIXES
            if (not next_all_empty) and not_new_section and (not is_section_heading) and is_continuation:
                merged_label = label + next_label
                result.append([merged_label] + list(next_values))
                i += 2
                continue
        result.append(row)
        i += 1
    return result


def _is_pure_amount_cell(cell):
    """判断单元格是否为纯金额数字(如 45,248,240.59 / 0.00 / -13,252,409.54)。
    标签文本(含中文/序号"1.")返回 False。"""
    s = str(cell).strip().replace(",", "")
    if not s:
        return False
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", s))


def _merge_leading_label_column(rows):
    """返工16修5: 合并"顶格标签溢出列"。

    PDF 财务报表的标签列里，顶格(无缩进)加粗科目(如"五、净利润""一、营业总收入"
    "1.归属于母公司股东的净利润")会被 pdfplumber 拆到第0列，而缩进科目(如"四、利润总额"
    "减：所得税费用""其中：营业收入")落在第1列(表头"项目"所在列)。
    filter_sparse_columns 会把稀疏的第0列当噪声删掉 → 顶格标签丢失 → net_profit/归母净利润
    无法匹配(万邦德"五、净利润"标签空、值仍在)。此处在过滤稀疏列前，把第0列(纯标签溢出)
    合并进第1列，保证标签不丢。
    """
    if not rows:
        return rows
    n_cols = max(len(r) for r in rows)
    if n_cols < 2:
        return rows
    # 表头(前2行)"项目"/"科目"所在列
    label_col = None
    for r in rows[:2]:
        for ci, c in enumerate(r):
            if ci >= n_cols:
                break
            s = str(c)
            if "项目" in s or "科目" in s:
                label_col = ci
                break
        if label_col is not None:
            break
    if label_col != 1:
        return rows
    # 第0列必须是"纯标签溢出": 存在非空、且非纯金额的单元格(即标签文本)
    spill_ok = False
    for r in rows:
        if len(r) > 0:
            c0 = str(r[0]).strip()
            if c0 and not _is_pure_amount_cell(c0):
                spill_ok = True
                break
    if not spill_ok:
        return rows
    merged = []
    for r in rows:
        c0 = str(r[0]).strip() if len(r) > 0 else ""
        c1 = str(r[1]).strip() if len(r) > 1 else ""
        label = (c0 + c1).strip() if (c0 and c1) else (c0 or c1)
        merged.append([label] + list(r[2:]))
    return merged


def preprocess_table(matrix):
    if not matrix or not matrix.get("data_rows"):
        return matrix
    header_row = matrix.get("header_row", [])
    data_rows = matrix.get("data_rows", [])
    all_rows = [header_row] + data_rows
    all_rows = _merge_leading_label_column(all_rows)
    all_rows = filter_sparse_columns(all_rows)
    if len(all_rows) >= 3:
        merged_header, new_data_rows = merge_multirow_header(all_rows)  # 返工15修1: 动态表头
    elif len(all_rows) >= 2:
        merged_header, new_data_rows = all_rows[0], all_rows[1:]
    else:
        merged_header, new_data_rows = all_rows[0] if all_rows else [], []
    new_data_rows = merge_split_rows(new_data_rows)
    matrix["header_row"] = merged_header
    matrix["data_rows"] = new_data_rows
    return matrix


def extract_from_paragraphs(pdf, page_idx, table_type=None):
    """段落式数据提取: 从文本中找关键词+数字"""
    try:
        text = pdf.pages[page_idx].extract_text() or ""
    except IndexError:
        return None
    if not text.strip():
        return None
    import re as _re
    from pdf_extractor.value_normalizer import parse_number as _pn
    found = {}
    key_patterns = [
        ("total_operating_revenue", "income_sheet", r"(?:营业总收入|营业收入|营收)[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
        ("net_profit", "income_sheet", r"(?:归母净利润|归属于上市公司股东的净利润|净利润)[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
        ("eps", "core_performance_indicators_sheet", r"基本每股收益[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
        ("roe_weighted_excl_non_recurring", "core_performance_indicators_sheet", r"加权平均净资产收益率[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)%"),
        ("asset_total_assets", "balance_sheet", r"(?:资产总计|总资产)[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
        ("total_operating_revenue", "core_performance_indicators_sheet", r"营业总收入[：:]\s*([+-]?[\d,]+(?:\\.\d+)?)"),
    ]
    for fname, tbl, pat in key_patterns:
        m = _re.search(pat, text)
        if m:
            v = _pn(m.group(1))
            if v is not None:
                tbl_data = found.setdefault(tbl, {})
                tbl_data[fname] = v
    return found if found else None
