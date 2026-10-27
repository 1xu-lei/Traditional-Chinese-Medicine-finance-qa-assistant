# -*- coding: utf-8 -*-
"""
file_parser.py: PDF文件名解析 + 内容元信息提取
上交所: 600085_20250404_9F44.pdf → code=600085, year=2025, period=Q1
深交所: 云南白药：2024年年度报告.pdf → name=云南白药, year=2024, period=FY
M0: 用pdf_segmenter分块后提取股票代码（不依赖外部映射、不硬编码范围）
"""
import os, re, json
import pdfplumber

DATE_TO_PERIOD = {1:"FY",2:"FY",3:"FY",4:"Q1",5:"Q1",6:"H1",7:"H1",8:"H1",9:"H1",10:"Q3",11:"Q3",12:"FY"}

# 返工17修1: 边界月份——文件名月份无法区分报告期，必须内容核实。
# 4月: 年报(上年FY, 4月底披露) vs 一季报(Q1)。10月: 三季报(Q3) vs 极少数补发的年报(FY)。
BOUNDARY_MONTHS = {4, 10}

# 内容核实关键词(最具体的排前面，防"半年度报告"被"年度报告"误匹配)
_CONTENT_PERIOD_ORDER = [
    ("第一季度报告", "Q1"), ("一季度报告", "Q1"), ("第1季度报告", "Q1"),
    ("第三季度报告", "Q3"), ("三季度报告", "Q3"), ("第3季度报告", "Q3"),
    ("半年度报告", "H1"), ("中期报告", "H1"),
    ("年度报告", "FY"),
]
_CONTENT_YEAR_RE = re.compile(
    r'(20\d{2})\s*年\s*(?:年度报告|半年度报告|第[一二三1-3]季度报告|一季度报告|三季度报告)'
)

# 股票代码提取模式（支持冒号和空格两种格式）
_CODE_PATS = [
    re.compile(r'股票代码[：:]\s*(\d{6})'),
    re.compile(r'股票代码\s+(\d{6})'),
    re.compile(r'证券代码[：:]\s*(\d{6})'),
    re.compile(r'证券代码\s+(\d{6})'),
    re.compile(r'A股代码[：:]\s*(\d{6})'),
]


def extract_code_from_pdf(fp, max_pages=15):
    """从PDF内容中提取股票代码（逐页扫描，找到即停）"""
    try:
        with pdfplumber.open(fp) as pdf:
            for i in range(min(max_pages, len(pdf.pages))):
                text = pdf.pages[i].extract_text() or ""
                for pat in _CODE_PATS:
                    m = pat.search(text)
                    if m:
                        return m.group(1)
    except:
        pass
    return None
CN_PERIOD = sorted([
    ("一季度报告","Q1"),("第一季度","Q1"),("一季报","Q1"),
    ("半年度报告","H1"),("半年度","H1"),("半年报","H1"),("中期报告","H1"),
    ("三季度报告","Q3"),("第三季度","Q3"),("三季报","Q3"),
    ("年度报告","FY"),("年报","FY"),("全年","FY"),
], key=lambda x: -len(x[0]))

def parse_sse(fn):
    name = os.path.splitext(fn)[0]
    parts = name.split("_")
    if len(parts) < 2: return None, None, None
    sc = parts[0]; ds = parts[1]
    if not ds.isdigit() or len(ds) < 8: return sc, None, None
    y = int(ds[:4]); m = int(ds[4:6])
    rp = DATE_TO_PERIOD.get(m, "FY")
    ry = y-1 if (rp=="FY" and m<=4) else y
    return sc, ry, rp

def parse_sse_candidates(fn):
    """返工19: 返回 SSE 文件名可能对应的 (year, period) 候选列表。

    边界月份一个文件名可能对应两种报告期：
      - 4月: 上一年年报(FY) 或 当年一季报(Q1)
      - 10月: 当年三季报(Q3) 或 极少数补发年报(FY)
    返回全部候选，由调用方(find_pdf)用内容核实定夺。
    非边界月份返回单一候选 [(ry, rp)]。
    """
    name = os.path.splitext(fn)[0]
    parts = name.split("_")
    if len(parts) < 2:
        return []
    ds = parts[1]
    if not ds.isdigit() or len(ds) < 8:
        return []
    y = int(ds[:4]); m = int(ds[4:6])
    if m == 4:
        return [(y - 1, "FY"), (y, "Q1")]
    if m == 10:
        return [(y, "Q3"), (y, "FY")]
    rp = DATE_TO_PERIOD.get(m, "FY")
    ry = y - 1 if (rp == "FY" and m <= 4) else y
    return [(ry, rp)]

def parse_szse(fn):
    name = os.path.splitext(fn)[0]
    m = re.match(r"(.+?)[：:]\s*(\d{4})年(.+)", name)
    if m:
        c = m.group(1).strip(); y = int(m.group(2)); ps = m.group(3)
        for kw,code in CN_PERIOD:
            if kw in ps: return c, y, code
        return c, y, "FY"
    m2 = re.search(r"(\d{4})年", name)
    if m2:
        y = int(m2.group(1))
        for kw,code in CN_PERIOD:
            if kw in name: return name.split(kw)[0].strip()[:20], y, code
    return None, None, None


def _sse_filename_year_month(fn):
    """从 SSE 文件名 code_YYYYMMDD_xxx.pdf 提取 (年份, 月份)。"""
    name = os.path.splitext(fn)[0]
    parts = name.split("_")
    if len(parts) < 2:
        return None, None
    ds = parts[1]
    if not ds.isdigit() or len(ds) < 8:
        return None, None
    return int(ds[:4]), int(ds[4:6])


def _verify_period_by_content(fp):
    """返工17修1: 边界月份(4/10月)报告期内容核实。

    打开前4页，按最具体关键词优先判定报告期，并抽取报告年份。
    返回 (rp, report_year) 或 (None, None)。
    """
    try:
        with pdfplumber.open(fp) as pdf:
            text = "\n".join((pdf.pages[i].extract_text() or "")
                             for i in range(min(4, len(pdf.pages))))
    except Exception:
        return None, None
    if not text:
        return None, None
    rp = None
    for kw, code in _CONTENT_PERIOD_ORDER:
        if kw in text:
            rp = code
            break
    ry = None
    m = _CONTENT_YEAR_RE.search(text)
    if m:
        ry = int(m.group(1))
    return rp, ry

def classify_pdf(fp):
    """
    PDF 类型分类（新版：基于内容扫描，2026-08-08 重构）

    返回 dict: {
        "report_type": "FY"|"H1"|"Q1"|"Q3"|"unknown",
        "report_format": "full"|"summary"|"unknown",
        "sheet_variant": "consolidated_only"|"both"|"parent_only"|"unknown",
        "page_category": "tiny"|"medium"|"large"|"xlarge",
        "stock_code": "600085" or None,
        "company_name": "同仁堂" or None,
        "report_year": 2022 or None,
        "total_pages": 203,
        "confidence": 0.95,
    }

    注意：这是新版分类器。旧版仅返回 "summary"/"full" 字符串，
    保持向后兼容——新版 dict 包含 "report_type" 和 "report_format" 键。
    """
    try:
        from pdf_extractor.pdf_classifier import classify_pdf as new_classify
        classification = new_classify(fp)
        return {
            "report_type": classification.report_type,
            "report_format": classification.report_format,
            "sheet_variant": classification.sheet_variant,
            "page_category": classification.page_category,
            "stock_code": classification.stock_code,
            "company_name": classification.company_name,
            "report_year": classification.report_year,
            "total_pages": classification.total_pages,
            "confidence": classification.confidence,
            "is_summary": classification.is_summary(),
            "is_cumulative": classification.is_cumulative,
        }
    except Exception as e:
        # 新版分类器不可用时，退回旧逻辑
        sz = os.path.getsize(fp)
        fn = os.path.basename(fp)
        is_s = "摘要" in fn or sz < 200*1024
        return {
            "report_type": "unknown",
            "report_format": "summary" if is_s else "full",
            "sheet_variant": "unknown",
            "page_category": "tiny" if sz < 200*1024 else "unknown",
            "stock_code": None,
            "company_name": None,
            "report_year": None,
            "total_pages": 0,
            "confidence": 0.3,
            "is_summary": is_s,
            "is_cumulative": False,
        }

def get_all_pdfs(data_dir, use_classifier=False):
    """
    扫描目录返回所有 PDF 文件信息列表。

    参数:
      data_dir: PDF 数据目录
      use_classifier: 是否对每个PDF跑新版分类器（默认False，太慢；进提取循环再逐个分类）

    返回:
      [{fp, fn, ex, code, name, yr, rp, sz, type, ...}, ...]
      当 use_classifier=False 时，type 是旧版快速字符串 "summary"/"full"
      当 use_classifier=True 时，type 是新版 dict
    """
    results = []
    for root, dirs, files in os.walk(data_dir):
        for f in files:
            if not f.endswith(".pdf"): continue
            fp = os.path.join(root, f)
            if "上交所" in root:
                code, yr, rp = parse_sse(f)
                # 返工17修1: 边界月份(4/10月)文件名月份无法区分年报/一季报/三季报，必须内容核实。
                # 4月底发布的年报(如片仔癀 600436_20250426)会被 DATE_TO_PERIOD[4]=Q1 误判成 Q1，
                # 导致 Q1 营收=上年全年、M7 单调性断裂。内容核实后修正 rp 与年份。
                period_conf = "high"
                y, m = _sse_filename_year_month(f)
                if m in BOUNDARY_MONTHS:
                    verified_rp, content_yr = _verify_period_by_content(fp)
                    if verified_rp is not None:
                        rp = verified_rp
                        if content_yr is not None:
                            yr = content_yr
                        elif verified_rp == "FY" and y is not None and m <= 4:
                            yr = y - 1
                    else:
                        period_conf = "low"  # 无法核实，保留文件名解析但不静默顶替
                pdf_type = classify_pdf(fp) if use_classifier else ("summary" if "摘要" in f or os.path.getsize(fp) < 200*1024 else "full")
                results.append({"fp":fp,"fn":f,"ex":"SSE","code":code,"name":None,"yr":yr,"rp":rp,"sz":os.path.getsize(fp),"type":pdf_type,"report_period_conf":period_conf})
            elif "深交所" in root:
                nm, yr, rp = parse_szse(f)
                sc = extract_code_from_pdf(fp)
                pdf_type = classify_pdf(fp) if use_classifier else ("summary" if "摘要" in f or os.path.getsize(fp) < 200*1024 else "full")
                results.append({"fp":fp,"fn":f,"ex":"SZSE","code":sc,"name":nm,"yr":yr,"rp":rp,"sz":os.path.getsize(fp),"type":pdf_type,"report_period_conf":"high"})
    return results
