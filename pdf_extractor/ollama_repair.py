# -*- coding: utf-8 -*-
"""
ollama_repair.py: Ollama 兜底层 v2.0 — 候选行法 + 失败清单

用途：规则管线提取失败的字段，用本地 Ollama 模型兜底补齐。
定位：独立修复层，不接进 extract_sse/extract_szse 主流程。

特性：
  - 候选行法：只把正则抠出的科目候选行喂给模型（不喂整页）
  - 单位检测 + 统一换算万元
  - 恒等式(A=L+E)复验，不满足不写回
  - think:false 必须保留
  - 用 urllib，不引入新依赖

用法：
  python pdf_extractor/ollama_repair.py --check                 # 扫描生成失败清单
  python pdf_extractor/ollama_repair.py --json <file>           # 单文件直测(不写回)
  python pdf_extractor/ollama_repair.py --limit 10 --apply      # 批量修复前10个
"""
import os, sys, json, re, time, argparse, pdfplumber

# ==== 配置 ====
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

MODEL = "qwen3.5:9b"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
REPORTS_DIR = os.path.join(BASE, "data", "附件2：财务报告")
EXTRACT_DIR = os.path.join(BASE, "data", "extracted")
OUTPUT_FILE = os.path.join(BASE, "data", "repair_list.json")

# 需要兜底的 11 个核心字段
REQUIRED = {
    "income_sheet": [
        "total_operating_revenue", "net_profit", "total_profit",
        "operating_profit", "operating_expense_cost_of_sales",
    ],
    "balance_sheet": [
        "asset_total_assets", "liability_total_liabilities", "equity_total_equity",
    ],
    "cash_flow_sheet": [
        "operating_cf_net_amount", "investing_cf_net_amount", "financing_cf_net_amount",
    ],
}

FIELD_CN = {
    "total_operating_revenue": "营业收入",
    "net_profit": "净利润",
    "total_profit": "利润总额",
    "operating_profit": "营业利润",
    "operating_expense_cost_of_sales": "营业成本",
    "asset_total_assets": "资产总计",
    "liability_total_liabilities": "负债合计",
    "equity_total_equity": "所有者权益合计",
    "_alt_equity_total_equity": "股东权益合计",  # 返工14: 部分公司用"股东权益合计"
    "operating_cf_net_amount": "经营活动产生的现金流量净额",
    "investing_cf_net_amount": "投资活动产生的现金流量净额",
    "financing_cf_net_amount": "筹资活动产生的现金流量净额",
}

TBL_CN = {
    "income_sheet": "利润表",
    "balance_sheet": "资产负债表",
    "cash_flow_sheet": "现金流量表",
}

TITLE_KW = {
    "income_sheet": ["合并利润表", "利润表"],
    "balance_sheet": ["合并资产负债表", "资产负债表"],
    "cash_flow_sheet": ["合并现金流量表", "现金流量表"],
}

STOP_KW = {
    "income_sheet": ["母公司利润表", "资产负债表", "现金流量表"],
    "balance_sheet": ["母公司资产负债表", "利润表", "现金流量表"],
    "cash_flow_sheet": ["母公司现金流量表", "所有者权益变动表", "利润表", "资产负债表"],
}

NOISE = [
    "客户存款", "保单红利", "保户储金", "拆入资金", "向中央银行",
    "再保", "保险合同准备金", "吸收存款", "同业存放",
]

# 子串陷阱：这些短词会误匹配到长标签（如"负债合计"匹配到"流动负债合计"）
SUBSTRING_TRAPS = {
    "负债合计": ["流动负债合计", "非流动负债合计"],
    "资产总计": [],  # 通常不会误匹配
    "所有者权益合计": ["归属于母公司所有者权益合计", "少数股东所有者权益合计"],
    # 返工14v4: "股东权益合计"是"归属于母公司股东权益合计"的子串，必须先排除
    "股东权益合计": ["归属于母公司股东权益合计"],
}

def _clean_candidate(label, text):
    """检查候选文本是否是子串陷阱（如'负债合计'匹配到'流动负债合计'）"""
    traps = SUBSTRING_TRAPS.get(label, [])
    for trap in traps:
        if trap in text:
            return None  # 拒绝这个候选
    return text

VALID_META = {
    "income_sheet": {50000, 5000, 200000, 20000},
    "balance_sheet": {1000, 100000, 10000, 200000, 50000},
    "cash_flow_sheet": {5000, 200000, 20000},
}

AMOUNT_MIN, AMOUNT_MAX = 1.0, 1e8

# ==== 返工20: 定向修复表 (坏值清单 → PDF 实拍值) ====
# 值 = PDF实拍(元)/10000 = 万元; None = 置 null(源数据缺全文/单元格为空)。
# 证据来源: 返工单20-兜底sanity校验 修复要求#4。
TARGETED_FIXES = {
    # 1. 济川药业(600566) 2023FY: 语料只有年报摘要(600566_20240410_9Y58, 9页), 全文缺失。
    #    摘要p9"归母净利润282,278.12万元"被二次÷10000→28.2278; p3"利润总额3,473.0亿元"→0.3473。
    #    源数据缺全文无法正确修复 → 清空。
    "600566_2023FY_full.json": {
        "income_sheet": {"net_profit": None, "total_profit": None},
    },
    # 2. 万邦德(002082) 2023H1: 合并利润表p71 本期(第一列)
    #    利润总额25,818,192.74 / 净利润29,662,546.61 / 营业成本444,774,432.51 (元)。
    #    (所得税费用-3,844,353.87为负, 故净利润>利润总额, 属正常。)
    "万邦德_2023H1_full.json": {
        "income_sheet": {
            "total_profit": 2581.8193,
            "net_profit": 2966.2547,
            "operating_expense_cost_of_sales": 44477.4433,
        },
    },
    # 3. 长药控股(300391) 2024H1: 合并利润表p16 本期 营业成本69,482,031.98元
    #    (上期397,395,765.81元被误取成39739.5766)。
    "长药控股_2024H1_full.json": {
        "income_sheet": {"operating_expense_cost_of_sales": 6948.2032},
    },
    # 4. 新光药业(300519) 2024Q1: 筹资活动净额单元格为空, 抓了页码"10"→0.001 → 清空。
    "新光药业_2024Q1_full.json": {
        "cash_flow_sheet": {"financing_cf_net_amount": None},
    },
    # 5. 嘉应制药(002198) 2023FY: 兜底取了母公司表p83"-445.34"→-0.0445,
    #    应为合并表p84"22,988,954.84"元 → 2298.8955万。(2024Q1=-86.07元为真实值, 不动)
    "嘉应制药_2023FY_full.json": {
        "cash_flow_sheet": {"financing_cf_net_amount": 2298.8955},
    },
    # 6. 603998 2024FY: BS取了上期列。合并资产负债表p115 本期:
    #    负债合计1,531,705,165.94元 / 所有者权益合计1,734,868,298.65元;
    #    合并利润表p119 净利润249,848,866.44元(合并口径)。
    "603998_2024FY_full.json": {
        "balance_sheet": {
            "liability_total_liabilities": 153170.5166,
            "equity_total_equity": 173486.8299,
        },
        "income_sheet": {"net_profit": 24984.8866},
    },
}

# ==== PDF 查找 ====

_pdf_cache = {}

def _period_from_filename_sse(fn):
    """从 SSE 文件名日期推断报告期，返回 (year, period) 或 (None, None)"""
    from pdf_extractor.file_parser import parse_sse
    return parse_sse(fn)[1:]

def _period_from_filename_szse(fn):
    """从 SZSE 文件名文本推断报告期"""
    from pdf_extractor.file_parser import parse_szse
    return parse_szse(fn)[1:]

def _content_verify_period(pdf_path, expected_rp):
    """打开 PDF 前3页验证报告期：FY→年度报告 H1→半年度报告 Q1→第一季度报告 Q3→第三季度报告"""
    rp_map = {"FY": "年度报告", "H1": "半年度报告", "Q1": "第一季度报告", "Q3": "第三季度报告"}
    expected_text = rp_map.get(expected_rp, "")
    if not expected_text:
        return False
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i in range(min(3, len(pdf.pages))):
                t = pdf.pages[i].extract_text() or ""
                if expected_text in t:
                    return True
                # 摘要版也有"年度报告摘要"，内容含"年度报告"也算
                if expected_rp == "FY" and "年度报告" in t:
                    return True
                if expected_rp == "H1" and "半年度报告" in t:
                    return True
                if expected_rp == "Q1" and "第一季度报告" in t:
                    return True
                if expected_rp == "Q3" and "第三季度报告" in t:
                    return True
    except Exception:
        pass
    return False

def _is_summary_pdf(fp):
    """返工20: 判断是否摘要版(年度报告摘要)。
    摘要版无三主表，兜底用其值会写坏(如600566摘要的双重换算)。"""
    if "摘要" in os.path.basename(fp):
        return True
    # 全文报告通常 > 1MB，摘要版 < 500KB；大文件直接判非摘要，省去开PDF
    if os.path.getsize(fp) > 1_000_000:
        return False
    try:
        with pdfplumber.open(fp) as pdf:
            n = len(pdf.pages)
            first = pdf.pages[0].extract_text() or ""
    except Exception:
        return False
    return n < 20 and "报告摘要" in first

def find_pdf(code, abbr, ex, yr, rp):
    """根据股票代码/简称/年份/报告期找 PDF 路径。返工14: 按报告期词+内容验证筛选。"""
    key = f"{code}_{yr}{rp}_{ex}"
    if key in _pdf_cache:
        return _pdf_cache[key]
    subdir = "reports-上交所" if ex == "sse" else "reports-深交所"
    pdir = os.path.join(REPORTS_DIR, subdir)
    if not os.path.isdir(pdir):
        _pdf_cache[key] = None
        return None

    from pdf_extractor.file_parser import parse_szse, parse_sse_candidates, _verify_period_by_content
    candidates = []  # [(path, size, content_confirmed), ...]
    if ex == "sse":
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith(".pdf") or not fn.startswith(code + "_"):
                continue
            fp = os.path.join(pdir, fn)
            cand_periods = parse_sse_candidates(fn)
            if (yr, rp) not in cand_periods:
                continue
            # 返工19: 边界月份(4/10月)文件名歧义(年报 vs 季报)，内容核实定夺。
            # 复用返工17修1 的 _verify_period_by_content，优先匹配更具体关键词，
            # 避免把 4 月底的 Q1 报告(如 600080_20250425)误判成年报。
            if len(cand_periods) > 1:
                content_rp, content_yr = _verify_period_by_content(fp)
                if content_rp != rp:
                    continue
                if content_yr is not None and content_yr != yr:
                    continue
                candidates.append((fp, os.path.getsize(fp), True))
            else:
                candidates.append((fp, os.path.getsize(fp), False))
    else:
        ab = (abbr or "").replace(" ", "")
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith(".pdf"):
                continue
            if ab and ab not in fn.replace(" ", ""):
                continue
            nm, y, p = parse_szse(fn)
            if y == yr and p == rp:
                fp = os.path.join(pdir, fn)
                candidates.append((fp, os.path.getsize(fp), False))

    # 返工20: 摘要版拒绝——摘要版无三主表，兜底用其值会写坏(如600566摘要双重换算)。
    candidates = [(fp, sz, cf) for fp, sz, cf in candidates if not _is_summary_pdf(fp)]
    if not candidates:
        _pdf_cache[key] = None
        return None

    # 单候选直接返回
    if len(candidates) == 1:
        _pdf_cache[key] = candidates[0][0]
        return _pdf_cache[key]

    # 多候选时：优先文件名含报告期词的；其次内容验证
    rp_keywords = {"FY": ["年度报告", "年报"], "H1": ["半年度报告", "半年报", "中期报告"],
                   "Q1": ["第一季度报告", "一季度报告", "一季报"],
                   "Q3": ["第三季度报告", "三季度报告", "三季报"]}
    kw_list = rp_keywords.get(rp, [])

    scored = []  # [(path, size, score)]
    for fp, sz, _ in candidates:
        fn = os.path.basename(fp)
        score = 0
        # 文件名含关键词加分
        for kw in kw_list:
            if kw in fn:
                score += 10
                break
        # 文件名含"摘要"扣分（摘要版数据不全）
        if "摘要" in fn:
            score -= 5
        # 内容验证：打开前3页核实
        if _content_verify_period(fp, rp):
            score += 5
        scored.append((fp, sz, score))

    # 按 score 降序，同分取较大文件
    scored.sort(key=lambda x: (-x[2], -x[1]))
    best = scored[0][0]
    if len(scored) > 1:
        print(f"  [find_pdf] {len(scored)}候选 -> {os.path.basename(best)} (score={scored[0][2]})")
    _pdf_cache[key] = best
    return best


# ==== 页面定位 ====

def _find_true_table_start(pdf, table_type, n=None):
    """返工14v4: 扫描PDF找独立标题行(非目录/非附注)，验证页面含表数据+数字，取第一个通过验证的页。

    核心洞察：目录引用在前(含"…"或裸页码)、附注引用在后(长段落含关键词)，
    独立标题行(≤25字短行)在中间。取"第一个"而非"最后一个"，自然避开附注区。
    接收已打开的 pdfplumber 对象，避免重复打开 PDF。
    """
    titles = TITLE_KW.get(table_type, [])
    if not titles:
        return None
    if n is None:
        n = len(pdf.pages)

    # 真表页必备：这些科目行+数字
    verify_fields = {
        "balance_sheet": ["资产总计", "负债合计", "股东权益合计", "所有者权益合计", "负债和所有者权益"],
        "income_sheet": ["营业收入", "营业利润", "利润总额", "净利润", "营业总成本"],
        "cash_flow_sheet": ["经营活动产生的现金流量净额", "经营活动现金流量净额",
                           "投资活动产生的现金流量净额", "筹资活动产生的现金流量净额"],
    }

    # Pass 1: 候选页 — 含独立标题行(边扫边验证，第一个通过即返回)
    candidates = []
    verify_set = verify_fields.get(table_type, [])

    for i in range(n):
        t = pdf.pages[i].extract_text() or ""
        found_candidate = False
        for line in t.splitlines():
            ls = line.strip()
            if not ls or len(ls) > 25:  # 真标题行短；附注段落长
                continue
            for title in titles:
                if title not in ls:
                    continue
                # 目录引用: "合并资产负债表 ………… 120" 或 "资产负债表 ........ 88"
                if re.search(r'[\.…]{2,}', ls):
                    continue
                # 目录引用变体: "合并资产负债表 120"(标题+纯页码)
                rest = ls.replace(title, "").strip()
                if re.match(r'^\d{1,4}$', rest):
                    continue
                candidates.append(i)
                found_candidate = True
                break
            if found_candidate:
                break

        # 边扫边验证：每积累一个候选，检查它和下2页
        for cp in candidates:
            for offset in range(3):
                check_page = cp + offset
                if check_page >= n:
                    break
                check_text = pdf.pages[check_page].extract_text() or ""
                for vf in verify_set:
                    idx = check_text.find(vf)
                    if idx < 0:
                        continue
                    # 必须出现在短行中(≤80字)，附注段落往往>80字
                    line_start = check_text.rfind("\n", 0, idx) + 1
                    line_end = check_text.find("\n", idx)
                    if line_end < 0:
                        line_end = len(check_text)
                    if line_end - line_start > 80:
                        continue
                    # 附近有数字(≥3位，排除页码)
                    nearby = check_text[max(0, idx-5):idx + len(vf) + 80]
                    if re.search(r'\d[\d,.]{2,}', nearby):
                        return cp  # 第一个通过验证的候选 = 真表起始

    # Pass 2: 兜底 — 第一个候选(即使未通过验证)
    if candidates:
        return candidates[0]
    return None


def locate_pages_text(pdf_path, table_type, max_pages=20):
    """返回 [(page_no, [lines]), ...]，定位到目标表所在页。
    返工14v4: 独立标题行+数据验证取第一页；fallback section_pages；不再盲信sp[0]和"最后一个"。"""
    start = None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            n = len(pdf.pages)

            # 策略1: 独立标题行检测(返工14v4) — 共用已打开的 PDF
            start = _find_true_table_start(pdf, table_type, n)

            # 策略2: fallback section_pages
            if start is None:
                try:
                    from pdf_extractor.pdf_segmenter import get_section_pages
                    section_pages, _, _ = get_section_pages(pdf_path, None)
                    sp = section_pages.get(table_type)
                    if sp:
                        start = sp[0]
                except Exception:
                    pass

            if start is None:
                start = 0

            # 提取页面文本(复用已打开的PDF)
            out = []
            for i in range(start, min(start + max_pages, n)):
                t = pdf.pages[i].extract_text() or ""
                if t.strip():
                    out.append((i + 1, [ln for ln in t.splitlines() if ln.strip()]))
            return out
    except Exception as e:
        print(f"  !! PDF打开失败: {e}")
        return []


# ==== 候选行查找 (返工13 修复版) ====

def find_candidates_v2(pages, label):
    """
    在页行里找含 label(允许换行拆开) 的行。

    返工13 修复：不再拼 @@行号@@ 标记，改用 "\n".join + count("\n")。
    返回 [(page_no, 候选文本), ...]
    """
    # 标签允许字符间有任意空白/换行
    pat = re.compile(r"\s*".join(re.escape(c) for c in label))
    cands = []

    for pno, lines in pages:
        joined = "\n".join(lines)
        for m in pat.finditer(joined):
            # 用匹配起点前的换行符数量计算起/止行号
            start_line = joined[:m.start()].count("\n")
            end_line = joined[:m.end()].count("\n")
            # 取起始行到终点行（如果 label 跨了行），再补一行（数字可能在下一行）
            snippet_lines = lines[start_line:end_line + 2]
            snippet = " ".join(snippet_lines)
            snippet = re.sub(r"\s+", " ", snippet).strip()[:150]

            # 噪声过滤
            if any(n in snippet for n in NOISE):
                continue
            # 子串陷阱过滤（如"负债合计"误匹配"流动负债合计"）
            cleaned = _clean_candidate(label, snippet)
            if cleaned is None:
                continue

            cands.append((pno, cleaned))
            break  # 每页每个 label 只取第一个匹配
    return cands


# ==== 单位检测 ====

def detect_unit(text):
    m = re.search(r'单位[：:]\s*(人民币)?(元|万元|亿元|千元)', text)
    if m:
        return {"元": 1, "万元": 10000, "亿元": 100000000, "千元": 1000}[m.group(2)]
    if "单位：元" in text or "币种：人民币" in text:
        return 1
    if "万元" in text and "元" in text:
        return 10000
    return 10000


# ==== Ollama 调用 ====

def ollama_chat(prompt, num_predict=200):
    """调用 Ollama /api/chat，think:false 必须保留"""
    import urllib.request as _ur
    payload = {
        "model": MODEL,
        "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_predict": num_predict, "temperature": 0},
    }
    body = json.dumps(payload).encode("utf-8")
    req = _ur.Request(OLLAMA_URL, data=body,
                      headers={"Content-Type": "application/json"})
    with _ur.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8")).get("message", {}).get("content", "")


def parse_json_response(resp):
    """从 Ollama 输出中提取 JSON 对象"""
    m = re.search(r'\{[^{}]*\}', resp, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        d = {}
        for k, v in re.findall(r'"([\w]+)"\s*[:：]\s*"?(-?[\d,\.]+)"?', m.group(0)):
            d[k] = v.replace(",", "")
        return d or None


# ==== 失败清单 (合并自 _repair_check.py) ====

def check_json(fp):
    """检查单个 JSON 文件的字段完整性和合理性"""
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception as e:
        return [{"reason": f"JSON损坏: {e}", "table": "?", "fields": []}]

    data = d.get("data", {}) if isinstance(d, dict) else {}
    fails = []
    for tt, fields in REQUIRED.items():
        kv = data.get(tt, {}) if isinstance(data, dict) else {}
        missing = [f for f in fields if f not in kv or kv.get(f) is None]
        bad_range = []
        for f in fields:
            v = kv.get(f)
            if v is None:
                continue
            try:
                v = float(v)
            except Exception:
                continue
            if not (AMOUNT_MIN <= abs(v) <= AMOUNT_MAX):
                bad_range.append(f"{f}={v}")
        if missing:
            fails.append({"table": tt, "reason": "缺失", "fields": missing})
        if bad_range:
            fails.append({"table": tt, "reason": "区间异常", "fields": bad_range})

    # 恒等式: A = L + E
    bs = data.get("balance_sheet", {}) if isinstance(data, dict) else {}
    try:
        a = float(bs["asset_total_assets"])
        l_val = float(bs["liability_total_liabilities"])
        e = float(bs["equity_total_equity"])
        if abs(e - (a - l_val)) > max(abs(e), 1) * 0.01:
            fails.append({
                "table": "balance_sheet",
                "reason": f"恒等式破 E={e:.2f} A-L={a-l_val:.2f}",
                "fields": ["equity_total_equity"],
            })
    except Exception:
        pass
    return fails


def generate_failure_list():
    """扫描 data/extracted 下所有 JSON，输出失败清单"""
    out = []
    for ex in ["sse", "szse"]:
        edir = os.path.join(EXTRACT_DIR, ex)
        if not os.path.isdir(edir):
            continue
        for fn in sorted(os.listdir(edir)):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(edir, fn)
            try:
                d = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            # 返工17修2: 摘要版(通常4页, 无利润表/资产负债表/现金流量表)按设计只提取
            # 核心业绩指标(CPI)。三主表字段"缺失"是设计使然，不是提取失败——
            # 若交给兜底层，find_pdf 会定位到同期的全文报告，用母公司表值补进摘要JSON，
            # 再经 import_db 覆盖全文版的正确合并值(如600479营收 18.2亿被4.88亿覆盖)。
            # 故摘要版直接跳过三主表兜底。
            if d.get("report_type") == "summary":
                continue
            fails = check_json(fp)
            if fails:
                out.append({
                    "file": fn, "path": fp, "ex": ex,
                    "code": d.get("stock_code"),
                    "abbr": d.get("stock_abbr"),
                    "yr": d.get("report_year"),
                    "rp": d.get("report_period"),
                    "fails": fails,
                })

    json.dump(out, open(OUTPUT_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    n_files = len(out)
    n_items = sum(len(f["fields"]) for o in out for f in o["fails"])
    print(f"失败清单: {n_files} 文件, {n_items} 项 → {OUTPUT_FILE}")
    for o in out[:15]:
        print(f"  [{o['ex']}] {o['file'][:40]} {o['code']} {o['rp']}")
        for f in o["fails"]:
            print(f"      {f['table']}: {f['reason']} {f['fields'][:5]}")
    if len(out) > 15:
        print(f"  ... 还有 {len(out) - 15} 个")
    return out


# ==== 兜底修复 ====

def _extract_first_value(seg, lbl):
    """返工20: 从候选文本提取第一个非占位数值(本期/期末列)。
    跳过 0.00 占位行；返回原始字符串(保留逗号/负号)或 None。"""
    after = seg.split(lbl)[-1] if lbl and lbl in seg else seg
    for m in re.finditer(r"(-?[\d,]+(?:\.\d+)?)", after):
        raw = m.group(1)
        try:
            if abs(float(raw.replace(",", ""))) < 0.01:  # 0.00 占位行，跳过
                continue
        except ValueError:
            continue
        return raw
    return None


def _sanity_check(field, val_wan, data):
    """返工20: 写回前 sanity 校验。返回 (ok, reason)。"""
    # 1) 量级: 金额字段 |value| < 1 万元 拒绝(0.00占位/页码/双重换算等)
    if abs(val_wan) < 1.0:
        return False, f"量级异常(|{val_wan}|<1万)"

    rev = data.get("income_sheet", {}).get("total_operating_revenue")
    try:
        rev = float(rev)
    except (TypeError, ValueError):
        rev = None

    if rev is not None and rev != 0:
        # 2) 成本/营收比率 ∈ (0, 1.5)
        if field == "operating_expense_cost_of_sales":
            ratio = abs(val_wan) / abs(rev)
            if ratio >= 1.5:
                return False, f"成本/营收比 {ratio:.2f} >= 1.5"
        # 3) 利润总额/净利 与 revenue 差 5 个量级以上(净利率<0.01% 或 >500%)
        if field in ("net_profit", "total_profit"):
            ratio = abs(val_wan) / abs(rev)
            if ratio < 1e-4:
                return False, f"利润/营收比 {ratio:.2e} 过低(<0.01%)"
            if ratio > 5.0:
                return False, f"利润/营收比 {ratio:.2f} 过高(>500%)"

    return True, ""


def _check_identity(data):
    """返工20: 恒等式复验 A = L + E。返回 (ok, detail)。"""
    bs = data.get("balance_sheet", {})
    try:
        a = float(bs["asset_total_assets"])
        l = float(bs["liability_total_liabilities"])
        e = float(bs["equity_total_equity"])
    except (KeyError, TypeError, ValueError):
        return False, "缺字段"
    if abs(e - (a - l)) > max(abs(e), 1) * 0.01:
        return False, f"E={e:.2f} A-L={a-l:.2f}"
    return True, ""


def apply_targeted_fixes(dry_run=True):
    """返工20: 对 6 个坏值文件定向写回 PDF 实拍值。None = 置 null。
    dry_run=True 只打印变更不写回。返回变更文件数。"""
    n = 0
    for fn, fix in TARGETED_FIXES.items():
        fp = None
        for ex in ["sse", "szse"]:
            cand = os.path.join(EXTRACT_DIR, ex, fn)
            if os.path.exists(cand):
                fp = cand
                break
        if fp is None:
            print(f"  [缺] {fn}")
            continue
        d = json.load(open(fp, encoding="utf-8"))
        data = d.setdefault("data", {})
        changed = []
        for tt, kv in fix.items():
            tbl = data.setdefault(tt, {})
            for fld, val in kv.items():
                old = tbl.get(fld)
                if val is None:
                    tbl.pop(fld, None)
                else:
                    tbl[fld] = val
                changed.append(f"{tt}.{fld}: {old} -> {val}")
        if not dry_run:
            json.dump(d, open(fp, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            n += 1
            print(f"  [写回] {fn}: " + "; ".join(changed))
        else:
            print(f"  [dry] {fn}: " + "; ".join(changed))
    if not dry_run:
        print(f"定向修复完成: {n} 文件")
    return n


def repair_file(entry, apply=False):
    """对单个失败条目的缺失字段进行 Ollama 兜底"""
    fp = entry["path"]
    code = entry["code"]
    abbr = entry.get("abbr") or entry.get("stock_abbr")
    ex = entry["ex"]
    yr = entry["yr"]
    rp = entry["rp"]

    pdf = find_pdf(code, abbr, ex, yr, rp)
    if not pdf:
        return {
            "file": entry["file"], "status": "SKIP",
            "why": f"找不到匹配({code},{yr}{rp})的PDF",
        }

    d = json.load(open(fp, encoding="utf-8"))
    data = d.setdefault("data", {})
    result = {"file": entry["file"], "pdf": pdf, "repaired": [], "failed": []}

    for f in entry["fails"]:
        tt = f["table"]
        fields = f["fields"] if isinstance(f["fields"], list) else [f["fields"]]
        if tt not in TBL_CN:
            continue
        # 只处理还没值的字段
        existing = data.get(tt, {}) if isinstance(data, dict) else {}
        fields = [x for x in fields if x in FIELD_CN and x not in existing]
        if not fields:
            continue

        pages = locate_pages_text(pdf, tt)
        all_text = "\n".join("\n".join(ls) for _, ls in pages)
        factor = detect_unit(all_text)

        # 候选行法: 正则抠候选行
        cand_map = {}
        label_used = {}  # 每个字段实际用的标签（equity 可能用"股东权益合计"）
        prompt_parts = []
        for fld in fields:
            lbl = FIELD_CN[fld]
            cands = find_candidates_v2(pages, lbl)
            # 返工14: equity 可能标为"股东权益合计"(如600332)而非"所有者权益合计"
            if not cands and fld == "equity_total_equity":
                lbl = FIELD_CN.get("_alt_equity_total_equity", "股东权益合计")
                cands = find_candidates_v2(pages, lbl)
            if not cands:
                result["failed"].append({
                    "table": tt, "fields": [fld],
                    "why": "文本中找不到该科目行",
                })
                continue
            cand_map[fld] = cands
            label_used[fld] = lbl
            prompt_parts.append(f"{lbl}（{fld}）：")
            for j, (pno, row) in enumerate(cands):
                prompt_parts.append(f"  [{fld}.{j+1}] 第{pno}页: {row}")

        if not prompt_parts:
            continue

        prompt = (
            "你是财务数据提取助手。以下是某医药行业公司（非银行非保险）"
            f"{TBL_CN[tt]}中相关科目的候选行（可能含母公司表/期初列）。\n"
            "每行通常含两个数值：【第一个=本期/期末】，第二个=上期/期初。\n"
            "对每个科目：严格提取【第一个数值】（本期/期末），"
            "忽略第二个数值（上期/期初）；优先合并表行（数值通常较大）；\n"
            "忽略母公司表；0.00 是占位无业务含义，忽略；\n"
            "数值原样输出（保留负号和逗号，不要换算单位、不要四舍五入）。\n"
            "严禁解释，只输出JSON，找不到填 null，格式：{"
            + ",".join(f'"{x}":"数值"' for x in fields)
            + "}。\n\n"
            + "\n".join(prompt_parts)
        )

        # 首次尝试
        resp = ollama_chat(prompt)
        parsed = parse_json_response(resp)

        # 重试
        if not parsed:
            resp2 = ollama_chat(
                prompt + "\n\n【警告】上次没输出JSON。这次必须只输出JSON。"
            )
            parsed = parse_json_response(resp2)
            resp = resp2

        if not parsed:
            # 模型输出无法解析，尝试正则兜底所有字段
            parsed = {}
            for fld in fields:
                if fld not in cand_map:
                    continue
                lbl = label_used.get(fld, FIELD_CN.get(fld, ""))
                for pno, seg in cand_map[fld]:
                    raw = _extract_first_value(seg, lbl)
                    if raw is not None:
                        parsed[fld] = raw
                        break
            if not parsed:
                result["failed"].append({
                    "table": tt, "fields": fields,
                    "why": "模型输出无法解析且正则兜底失败: " + resp[:100].replace("\n", " "),
                })
                continue

        for fld in fields:
            raw = parsed.get(fld)
            model_ok = raw is not None and str(raw).strip() != "" and str(raw).strip().lower() != "null"

            # 正则兜底：模型返回 null/空时，从候选行直接取第一个数值（本期/期末列）
            if not model_ok and fld in cand_map:
                lbl = label_used.get(fld, FIELD_CN.get(fld, ""))
                for pno, seg in cand_map[fld]:
                    raw = _extract_first_value(seg, lbl)
                    if raw is not None:
                        model_ok = True
                        break

            if not model_ok:
                result["failed"].append({
                    "table": tt, "fields": [fld], "why": "模型返回null且正则兜底失败",
                })
                continue
            try:
                val = float(str(raw).replace(",", "")) * factor / 10000.0
            except Exception:
                result["failed"].append({
                    "table": tt, "fields": [fld],
                    "why": f"无法转数字: {raw}",
                })
                continue
            # 返工20: 写回前 sanity 校验，坏值不写回
            ok, reason = _sanity_check(fld, val, data)
            if not ok:
                result["failed"].append({
                    "table": tt, "fields": [fld], "why": f"sanity拒绝: {reason}",
                })
                continue
            data.setdefault(tt, {})[fld] = round(val, 4)
            result["repaired"].append({
                "table": tt, "field": fld, "value_wan": round(val, 4), "raw": raw,
            })

    # 返工20: 恒等式复验——本次修复了 balance_sheet 字段时，FAIL 则回滚不写回
    bs_repaired = [x for x in result["repaired"] if x["table"] == "balance_sheet"]
    ok, detail = _check_identity(data)
    if bs_repaired and not ok:
        for x in bs_repaired:
            data["balance_sheet"].pop(x["field"], None)
        result["failed"].extend([{
            "table": "balance_sheet", "fields": [x["field"]],
            "why": f"恒等式复验失败({detail})，已回滚",
        } for x in bs_repaired])
        result["repaired"] = [x for x in result["repaired"] if x["table"] != "balance_sheet"]
        result["revalidate"] = f"FAIL 恒等式: {detail} (已回滚balance_sheet修复)"
    elif ok:
        result["revalidate"] = "OK 恒等式"
    else:
        result["revalidate"] = f"WARN 恒等式(预存): {detail} (非本次修复引入)"

    if apply:
        json.dump(d, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        result["applied"] = True

    return result


# ==== CLI ====

def main():
    ap = argparse.ArgumentParser(description="Ollama 兜底层 v2.0")
    ap.add_argument("--check", action="store_true", help="扫描生成失败清单")
    ap.add_argument("--json", help="单文件直测(不写回)")
    ap.add_argument("--limit", type=int, default=0, help="批量修复数量上限")
    ap.add_argument("--apply", action="store_true", help="写回JSON文件")
    ap.add_argument("--fix-targeted", action="store_true",
                    help="返工20: 定向修复6个坏值文件(配合--apply写回, 否则dry-run)")
    a = ap.parse_args()

    # 返工20: 定向修复坏值文件
    if a.fix_targeted:
        apply_targeted_fixes(dry_run=not a.apply)
        return

    # --check: 生成失败清单
    if a.check:
        generate_failure_list()
        return

    # --json: 单文件测试
    if a.json:
        d = json.load(open(a.json, encoding="utf-8"))
        fp = os.path.abspath(a.json)
        ex = "sse" if "sse" in fp.lower() else "szse"
        entry = {
            "file": os.path.basename(fp), "path": fp,
            "ex": ex,
            "code": d.get("stock_code"),
            "abbr": d.get("stock_abbr"),
            "yr": d.get("report_year"),
            "rp": d.get("report_period"),
            "fails": check_json(fp),
        }
        if not entry["fails"]:
            print("该文件无失败项，无需修复")
            return
        print("失败项:", [(f["table"], f["reason"], f["fields"]) for f in entry["fails"]])
        r = repair_file(entry, apply=a.apply)
        if r.get("status") == "SKIP":
            print(f"⏭ SKIP: {r.get('why')}")
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return

    # --limit [+--apply]: 批量修复
    list_path = OUTPUT_FILE
    if not os.path.exists(list_path):
        print(f"失败清单不存在: {list_path}\n请先运行 --check")
        return
    entries = json.load(open(list_path, encoding="utf-8"))
    if a.limit:
        entries = entries[:a.limit]
    print(f"待修复文件: {len(entries)} 个")
    results = []
    t0 = time.time()
    for i, e in enumerate(entries):
        print(f"\n[{i+1}/{len(entries)}] {e['file'][:45]} ({e['code']} {e['rp']})")
        r = repair_file(e, apply=a.apply)
        results.append(r)
        if r.get("status") == "SKIP":
            print(f"  ⏭ SKIP {r.get('why')}")
            continue
        for x in r.get("repaired", []):
            print(f"  ✓ {x['table']}.{x['field']} = {x['value_wan']}万 (raw={x['raw']})")
        for x in r.get("failed", []):
            print(f"  ✗ {x.get('table')} {x.get('fields')} ({x.get('why')})")
        if r.get("revalidate"):
            print(f"  复验: {r['revalidate']}")

    elapsed = time.time() - t0
    n_ok = sum(len(r.get("repaired", [])) for r in results)
    n_fail = sum(len(r.get("failed", [])) for r in results)
    n_skip = sum(1 for r in results if r.get("status") == "SKIP")
    print(f"\n耗时 {elapsed:.0f}s, 修复 {n_ok} 字段, 失败 {n_fail} 项, 跳过 {n_skip} 个")
    print(f"详细结果: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
