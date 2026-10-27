# -*- coding: utf-8 -*-
"""column_recognizer.py: M4 列报告期识别
支持格式:
  绝对期: 2022年、2021年、2020年、2024年9月30日
  相对期: 本年、上年、本期、上期、期末、期初
  累计期: 本年累计、上年同期累计、1-9月
  季度期: 一季度、第二季度、Q3、前三季度
"""
import re

YEAR_RE = re.compile(r"(20\d{2})年?")
MONTH_RE = re.compile(r"(\d{1,2})月(?:\d{1,2})?日?")

# 日期→报告期映射
DATE_TO_PERIOD = {"03":"Q1","04":"Q1","05":"Q1",
                  "06":"H1","07":"H1","08":"H1",
                  "09":"Q3","10":"Q3","11":"Q3",
                  "12":"FY","01":"FY","02":"FY"}

# 期间识别关键词
PERIOD_PATTERNS = [
    # 累计期（优先匹配）
    (r"(?:本年|本期|报告期)?(?:累计|1[-~]9月|1[-~]12月|前三季度|上半年)", "cumulative"),
    # 同比期
    (r"(?:上年同期|上期累计|去年同期|同比)", "yoy"),
    # 季度/半年度
    (r"(?:一|二|三|四)(?:季度|季)", "quarter"),
    (r"(?:第)?[1-4]季度?", "quarter"),
    (r"(?:半年度?|中期|H1)", "half"),
    (r"(?:前)?三季度?", "quarter"),
    (r"1[-~]9月", "quarter"),
    # 变化量
    (r"(?:增减|变动|差异)", "delta"),
    (r"(?:增减)?变动(?:幅度|率)?", "delta"),
]

# 相对期映射
# 注意：长key必须排在短key前面，避免子串误匹配（如"上年期末"含"期末"）
RELATIVE_MAP = [
    # target 列（长→短）
    ("本报告期","target"), ("本年累计","target"),
    ("上年期末","prev"), ("上年同期","prev"), ("上年年末","prev"),
    ("报告期","target"),
    ("本年","target"), ("本期","target"), ("期末","target"), ("年末","target"),
    # prev 列
    ("上年","prev"), ("上期","prev"), ("上一年","prev"),
    ("期初","prev"), ("年初","prev"),
    # delta 列
    ("增减","delta"), ("变动","delta"), ("同比","delta"),
]


def detect_column_type(text):
    """检测列类型: target/prev/delta/other"""
    # 先查相对期关键词（长key优先，避免子串误匹配）
    for kw, t in RELATIVE_MAP:
        if kw in text: return t
    # 检查绝对年份
    ym = YEAR_RE.search(text)
    if ym:
        year = int(ym.group(1))
        if "累计" in text or "1-9" in text or "1-12" in text:
            return "target_cumulative"
        return "absolute_year"
    # 季度关键词
    for pat, ptype in PERIOD_PATTERNS:
        if re.search(pat, text):
            return ptype
    return "other"


def parse_quarter(text, report_year, report_period):
    """解析季度文本，返回 (year, period, is_cumulative)"""
    # 绝对年份
    ym = YEAR_RE.search(text)
    y = int(ym.group(1)) if ym else None

    # 累计期
    if "累计" in text or "1-9" in text or "前三季度" in text:
        return (y, "Q3", True) if y else (report_year, "Q3", True)
    if "上半年" in text or "1-6" in text:
        return (y, "H1", True) if y else (report_year, "H1", True)
    if "1-12" in text:
        return (y, "FY", True) if y else (report_year, "FY", True)

    # 单季
    qmap = {"一季度":"Q1", "第一季度":"Q1", "第1季度":"Q1", "1季度":"Q1",
            "二季度":"Q2", "第二季度":"Q2", "第2季度":"Q2", "2季度":"Q2",
            "半年":"H1", "半年度":"H1", "中期":"H1", "H1":"H1",
            "三季度":"Q3", "第三季度":"Q3", "第3季度":"Q3", "3季度":"Q3",
            "四季度":"Q4", "第四季度":"Q4", "第4季度":"Q4", "4季度":"Q4",
            "全年":"FY", "年度":"FY", "FY":"FY"}
    for kw, p in qmap.items():
        if kw in text: return (y, p, False)

    return (y, report_period, False) if y else (report_year, report_period, False)


def classify(text, report_year, report_period):
    """分析单个列头文本，返回标注信息"""
    col_type = detect_column_type(text)

    if col_type == "target":
        return {"type": "target", "year": report_year, "period": report_period, "is_cumulative": False}
    elif col_type == "target_cumulative":
        y, p, cum = parse_quarter(text, report_year, report_period)
        return {"type": "target", "year": y or report_year, "period": p, "is_cumulative": cum}
    elif col_type == "prev":
        return {"type": "prev", "year": report_year - 1, "period": report_period, "is_cumulative": False}
    elif col_type == "delta":
        return {"type": "delta", "year": None, "period": None, "is_cumulative": False}
    elif col_type in ("quarter", "half", "absolute_year"):
        y, p, cum = parse_quarter(text, report_year, report_period)
        if y == report_year:
            return {"type": "target", "year": y, "period": p, "is_cumulative": cum}
        elif y == report_year - 1:
            return {"type": "prev", "year": y, "period": p, "is_cumulative": cum}
        else:
            return {"type": "other", "year": y, "period": p, "is_cumulative": cum}
    else:
        return {"type": "other", "year": None, "period": None, "is_cumulative": False}


def detect_fuzhu_and_adjust(header_row, target_col=1):
    """检测表头是否含附注/上期列，如有则跳到下一列
    事二修复: 扩展到跳过"上期/上年"列，避免取到上期金额
    """
    if not header_row: return target_col
    # 跳过列: 附注、上期、上年同期
    SKIP_COL_KW = [chr(38468), '上期', '上年', '上年同期']  # chr(38468)='附'
    for i, h in enumerate(header_row):
        if i == 0: continue  # 跳过标签列
        hdr_text = str(h)
        if any(kw in hdr_text for kw in SKIP_COL_KW):
            # 找下一个非跳过列
            for j in range(i + 1, len(header_row)):
                next_hdr = str(header_row[j])
                if not any(kw in next_hdr for kw in SKIP_COL_KW):
                    if next_hdr.strip():
                        return j
            return i + 1
    return target_col


def analyze(header_row, report_year, report_period):
    """分析列头，返回每列的标注信息"""
    target_col = detect_fuzhu_and_adjust(header_row)
    results = []
    for i, h in enumerate(header_row):
        if i == 0:
            results.append({"index": i, "text": h, "type": "label"})
        elif i == target_col:
            info = {"index": i, "text": h, **classify(h, report_year, report_period)}
            info["type"] = "target"
            results.append(info)
        else:
            results.append({"index": i, "text": h, **classify(h, report_year, report_period)})
    return results
