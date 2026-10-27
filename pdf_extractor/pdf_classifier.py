# -*- coding: utf-8 -*-
"""
pdf_classifier.py: PDF 类型分类器 — 扫描前几页确定报告类型/格式/元信息

核心理念（2026-08-08 重构）：
  不同 PDF 类型（年报/半年报/季报 × 完整版/摘要版）排版结构不同，
  必须先分类再选择提取策略，而不是一套逻辑硬套所有类型。

分类维度:
  report_type:   FY | H1 | Q1 | Q3          — 报告期间
  report_format: full | summary              — 完整版 vs 摘要版
  sheet_variant: consolidated_only | both    — 仅合并表 vs 合并+母公司双表
  page_count:    tiny(2-10) | medium(10-80) | large(80-200) | xlarge(200+)

输出示例:
  {
    "report_type": "FY",
    "report_format": "full",
    "sheet_variant": "both",        # "consolidated_only" | "both" | "unknown"
    "page_category": "large",
    "stock_code": "600085",
    "company_name": "同仁堂",
    "report_year": 2022,
    "total_pages": 203,
    "confidence": 0.95,
    "signals": ["年度报告全文", "公司代码:600085", "合并利润表+母公司利润表"],
  }
"""
import re
import pdfplumber
from dataclasses import dataclass, field
from typing import Optional, List, Dict


# ============================================================
# 分类信号定义
# ============================================================

# 报告类型信号（更具体的排前面，防止"半年度报告"被"年度报告"误匹配）
REPORT_TYPE_SIGNALS = [
    # (正则, 类型, 权重)
    # —— 季报（最具体，先匹配）——
    (r'第一季度报告', 'Q1', 12),
    (r'一季度报告', 'Q1', 10),
    (r'第1季度报告', 'Q1', 10),
    (r'第三季度报告', 'Q3', 12),
    (r'三季度报告', 'Q3', 10),
    (r'第3季度报告', 'Q3', 10),
    # —— 半年报（"半年度"必须在"年度"之前匹配）——
    (r'半年度报告全文', 'H1', 12),
    (r'半年度报告(?!摘要)', 'H1', 10),
    (r'中期报告', 'H1', 8),
    # —— 年报（最通用，放最后）——
    (r'年度报告全文', 'FY', 12),
    (r'年年度报告全文', 'FY', 12),
    (r'年度报告(?!摘要)', 'FY', 10),
    (r'年年度报告(?!摘要)', 'FY', 10),
]

# 摘要版信号
SUMMARY_SIGNALS = [
    (r'年度报告摘要', 10),
    (r'半年度报告摘要', 10),
    (r'本年度报告摘要来自年度报告全文', 15),  # 铁证
    (r'本半年度报告摘要来自半年度报告全文', 15),
]

# 公司/股票信息提取
STOCK_CODE_PATS = [
    re.compile(r'(?:公司代码|证券代码|股票代码|A股代码)[：:]\s*(\d{6})'),
    re.compile(r'(?:公司代码|证券代码|股票代码|A股代码)\s+(\d{6})'),
]
COMPANY_NAME_PATS = [
    re.compile(r'(?:公司简称|证券简称|股票简称)[：:]\s*(.+?)(?:\s|$)'),
    re.compile(r'(?:公司名称)[：:]\s*(.+?)(?:股份)?有限公司'),
]
REPORT_YEAR_PATS = [
    re.compile(r'(20\d{2})\s*年年度报告'),
    re.compile(r'(20\d{2})\s*年半年度报告'),
    re.compile(r'(20\d{2})\s*年第[一二三1-3]季度报告'),
    re.compile(r'(20\d{2})\s*年年度报告摘要'),
    re.compile(r'(20\d{2})\s*年度报告'),
]

# 页数类别
def _page_category(n):
    if n <= 10: return "tiny"
    if n <= 80: return "medium"
    if n <= 200: return "large"
    return "xlarge"


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PDFClassification:
    """PDF 分类结果"""
    report_type: str = "unknown"       # FY | H1 | Q1 | Q3
    report_format: str = "unknown"     # full | summary
    sheet_variant: str = "unknown"     # consolidated_only | both | parent_only
    page_category: str = "unknown"     # tiny | medium | large | xlarge
    stock_code: Optional[str] = None
    company_name: Optional[str] = None
    report_year: Optional[int] = None
    total_pages: int = 0
    confidence: float = 0.0
    signals: List[str] = field(default_factory=list)
    # 元信息（来自封面/扉页）
    metadata: Dict = field(default_factory=dict)

    def is_summary(self) -> bool:
        return self.report_format == "summary"

    def is_full(self) -> bool:
        return self.report_format == "full"

    def has_dual_variant(self) -> bool:
        """是否有合并+母公司双表"""
        return self.sheet_variant == "both"

    @property
    def is_cumulative(self) -> bool:
        """中国准则：中期报告采用累计口径（年初至报告期末）"""
        return self.report_type in ("Q1", "H1", "Q3")


# ============================================================
# 分类器
# ============================================================

class PDFClassifier:
    """PDF 类型分类器：扫描前 N 页，输出分类结果"""

    def __init__(self, scan_pages: int = 5):
        self.scan_pages = scan_pages

    def classify(self, pdf_path: str) -> PDFClassification:
        """
        主入口：对 PDF 进行分类

        参数:
          pdf_path: PDF 文件路径

        返回:
          PDFClassification 对象
        """
        try:
            pdf = pdfplumber.open(pdf_path)
        except Exception as e:
            return self._unknown_result(error=str(e))

        total_pages = len(pdf.pages)
        scan_n = min(self.scan_pages, total_pages)

        # 收集扫描页文本
        scanned_texts = []
        for i in range(scan_n):
            try:
                t = pdf.pages[i].extract_text() or ""
                scanned_texts.append(t)
            except Exception:
                scanned_texts.append("")

        # 合并文本（前 N 页）
        combined = "\n".join(scanned_texts)
        # 前两页通常包含最关键的分类信号
        first_two = "\n".join(scanned_texts[:2])

        result = PDFClassification(total_pages=total_pages)

        # === Step 1: 判断报告类型 (FY/H1/Q1/Q3) ===
        self._detect_report_type(first_two, result)

        # === Step 2: 判断完整版 vs 摘要版 ===
        self._detect_format(combined, first_two, result)

        # === Step 3: 提取股票代码/公司名/年份 ===
        self._extract_metadata(combined, first_two, result)

        # === Step 4: 判断页数类别 ===
        result.page_category = _page_category(total_pages)

        # === Step 5: 判断 sheet_variant (合并/母公司) ===
        # 完整版年报的 variant 信号在前5页可能找不到（在70+页），需要深扫
        deep_scan_text = combined
        if result.report_format == "full" and result.page_category in ("large", "xlarge"):
            deep_scan_text = self._deep_scan_variant(pdf, total_pages)
        self._detect_sheet_variant(deep_scan_text, result)

        # 补充：如果前5页没找到 stock_code，深扫找
        if not result.stock_code and result.report_format == "full":
            self._deep_scan_stock_code(pdf, total_pages, result)

        pdf.close()

        # === Step 6: 计算置信度 ===
        self._compute_confidence(result)

        return result

    def _deep_scan_variant(self, pdf, total_pages: int) -> str:
        """深扫：在完整版年报中找 variant 信号（密集跳页 + 目录页定位）"""
        texts = []
        # 策略1: 扫描更多页（每5页取1页，最多40页）
        step = max(1, total_pages // 40)
        found_consolidated = False
        found_parent = False
        for i in range(0, total_pages, step):
            try:
                t = pdf.pages[i].extract_text() or ""
                texts.append(t)
                if not found_consolidated:
                    found_consolidated = bool(re.search(
                        r'合并利润表|合并资产负债表|合并现金流量表', t))
                if not found_parent:
                    found_parent = bool(re.search(
                        r'(?<!合并)母公司利润表|(?<!合并)母公司资产负债表|母公司现金流量表', t))
                if found_consolidated and found_parent:
                    break
            except Exception:
                pass
        # 策略2: 如果跳页扫描没找到，从目录页推测（目录通常在p2-4，"第十节 财务报告"）
        if not (found_consolidated and found_parent) and total_pages > 50:
            for i in range(min(10, total_pages)):
                try:
                    t = pdf.pages[i].extract_text() or ""
                    if re.search(r'第十[节章].*财务报告|财务报告.*\d+', t):
                        # 目录中列出了 "第十节 财务报告"(page N) → 从该页开始密集扫
                        texts.append(f"[TOC_page_{i}] {t[:500]}")
                        break
                except Exception:
                    pass
        return "\n".join(texts)

    def _deep_scan_stock_code(self, pdf, total_pages: int, result: PDFClassification):
        """深扫：在完整版年报中找股票代码（通常在第4-7页的公司简介部分）"""
        for i in range(min(15, total_pages)):
            try:
                t = pdf.pages[i].extract_text() or ""
                for pat in STOCK_CODE_PATS:
                    m = pat.search(t)
                    if m:
                        result.stock_code = m.group(1)
                        result.signals.append(f"stock_code_deep:{result.stock_code}")
                        return
            except Exception:
                pass

    def _detect_report_type(self, text: str, result: PDFClassification):
        """从文本中检测报告类型"""
        best_type, best_weight = "unknown", 0
        for pattern, rtype, weight in REPORT_TYPE_SIGNALS:
            if re.search(pattern, text):
                if weight > best_weight:
                    best_type, best_weight = rtype, weight
                    result.signals.append(f"report_type:{rtype}(w={weight})")

        # SSE 文件：如果没有明确的报告类型关键词，从日期推断
        # 这个由 file_parser 的日期解析补充

        result.report_type = best_type

    def _detect_format(self, combined: str, first_two: str, result: PDFClassification):
        """判断是完整版还是摘要版"""
        summary_score = 0

        # 1. 明确信号
        for pattern, weight in SUMMARY_SIGNALS:
            if re.search(pattern, first_two):
                summary_score += weight
                result.signals.append(f"summary_signal:{pattern[:30]}(w={weight})")

        # 2. 摘要版典型结构：前两页出现 "主要会计数据" 和 "主要财务指标" 紧密相邻
        if "主要会计数据" in first_two and "主要财务指标" in first_two:
            # 完整版年报也有这些，但通常在第5-8页；摘要版在第1-3页
            if result.total_pages <= 10:
                summary_score += 5
                result.signals.append("summary:early_cpi+small_page")

        # 3. 小文件通常不是完整版年报（完整版年报至少30-40页）
        if result.total_pages <= 8:
            summary_score += 3

        # 4. 全文搜索：完整版年报一定有 "董事会报告" 或 "管理层讨论与分析"
        has_mgmt_discussion = bool(re.search(r'管理层讨论|董事会报告|公司治理', combined))
        if not has_mgmt_discussion and result.total_pages <= 30:
            summary_score += 4
            result.signals.append("summary:no_mgmt_discussion")

        # 5. "第十节 财务报告" 是完整版年报的标志（摘要版没有）
        has_financial_section = bool(re.search(r'第[十\d]+节\s*财务报告', combined))
        if has_financial_section:
            summary_score -= 20  # 铁证：是完整版
            result.signals.append("full:has_financial_section")

        if summary_score >= 10:
            result.report_format = "summary"
        elif summary_score <= -10:
            result.report_format = "full"
        elif result.total_pages <= 8:
            result.report_format = "summary"
        else:
            result.report_format = "full"

    def _extract_metadata(self, combined: str, first_two: str, result: PDFClassification):
        """提取股票代码、公司名称、报告年份"""
        # 股票代码
        for pat in STOCK_CODE_PATS:
            m = pat.search(first_two)
            if m:
                result.stock_code = m.group(1)
                result.signals.append(f"stock_code:{result.stock_code}")
                break

        # 公司名称
        for pat in COMPANY_NAME_PATS:
            m = pat.search(first_two)
            if m:
                name = m.group(1).strip()
                # 清理：去掉可能的尾随空格和标点
                name = re.sub(r'\s+', '', name)
                if len(name) <= 10:  # 合理的中文简称长度
                    result.company_name = name
                    result.signals.append(f"company:{name}")
                    break

        # 如果没找到简称，尝试从标题行提取
        if not result.company_name:
            m = re.search(r'(.+?)(?:股份)?有限公司\s*20\d{2}\s*年', first_two)
            if m:
                name = m.group(1).strip()
                if len(name) >= 4:
                    result.company_name = name + "股份有限公司"
                    result.signals.append(f"company_from_title:{name}")

        # 报告年份
        for pat in REPORT_YEAR_PATS:
            m = pat.search(first_two)
            if m:
                result.report_year = int(m.group(1))
                result.signals.append(f"year:{result.report_year}")
                break

    def _detect_sheet_variant(self, combined: str, result: PDFClassification):
        """
        判断报表类型：仅合并表 / 合并+母公司双表

        信号：
        - "合并利润表" + "母公司利润表" → both
        - "合并资产负债表" + "母公司资产负债表" → both
        - 只有 "合并利润表" → consolidated_only
        - 摘要版 → consolidated_only (摘要通常只有合并数据)
        """
        if result.report_format == "summary":
            result.sheet_variant = "consolidated_only"
            result.signals.append("variant:summary→consolidated_only")
            return

        has_consolidated = bool(
            re.search(r'合并利润表|合并资产负债表|合并现金流量表', combined)
        )
        has_parent = bool(
            re.search(r'母公司利润表|母公司资产负债表|母公司现金流量表', combined)
        )

        if has_consolidated and has_parent:
            result.sheet_variant = "both"
            result.signals.append("variant:both(合并+母公司)")
        elif has_consolidated:
            result.sheet_variant = "consolidated_only"
            result.signals.append("variant:consolidated_only")
        elif has_parent:
            result.sheet_variant = "parent_only"
            result.signals.append("variant:parent_only")
        else:
            # 季报/H1通常只有合并表
            if result.report_type in ("Q1", "Q3"):
                result.sheet_variant = "consolidated_only"
                result.signals.append("variant:quarterly→consolidated_only")
            else:
                result.sheet_variant = "unknown"

    def _compute_confidence(self, result: PDFClassification):
        """计算分类置信度"""
        score = 0.0
        max_score = 0.0

        # 报告类型置信度
        max_score += 15
        if result.report_type != "unknown":
            type_weights = {"FY": 12, "H1": 12, "Q1": 12, "Q3": 12}
            score += type_weights.get(result.report_type, 0)

        # 格式置信度
        max_score += 15
        if result.report_format != "unknown":
            score += 12

        # 股票代码
        max_score += 10
        if result.stock_code:
            score += 10

        # 年份
        max_score += 10
        if result.report_year:
            score += 10

        result.confidence = min(score / max(max_score, 1), 1.0)

    @staticmethod
    def _unknown_result(error: str = "") -> PDFClassification:
        return PDFClassification(
            report_type="unknown",
            report_format="unknown",
            confidence=0.0,
            signals=[f"error:{error}"],
        )


# ============================================================
# 单例 + 便捷函数
# ============================================================

_classifier: Optional[PDFClassifier] = None


def get_classifier() -> PDFClassifier:
    global _classifier
    if _classifier is None:
        _classifier = PDFClassifier()
    return _classifier


def classify_pdf(pdf_path: str) -> PDFClassification:
    """便捷函数：分类一个 PDF"""
    return get_classifier().classify(pdf_path)


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    import sys, os
    test_files = [
        # SZSE
        r"data\附件2：财务报告\reports-深交所\东阿阿胶：2022年年度报告.pdf",
        r"data\附件2：财务报告\reports-深交所\东阿阿胶：2022年年度报告摘要.pdf",
        r"data\附件2：财务报告\reports-深交所\东阿阿胶：2023年半年度报告.pdf",
        r"data\附件2：财务报告\reports-深交所\东阿阿胶：2023年一季度报告.pdf",
        # SSE
        r"data\附件2：财务报告\reports-上交所\600085_20230328_GGS9.pdf",
        r"data\附件2：财务报告\reports-上交所\600252_20230329_84OV.pdf",
    ]

    for fp in test_files:
        if not os.path.exists(fp):
            print(f"  SKIP (not found): {fp}")
            continue
        c = classify_pdf(fp)
        fn = os.path.basename(fp)[:50]
        print(f"\n{'='*60}")
        print(f"文件: {fn}")
        print(f"类型: {c.report_type} | 格式: {c.report_format} | variant: {c.sheet_variant}")
        print(f"公司: {c.company_name} | 代码: {c.stock_code} | 年份: {c.report_year}")
        print(f"页数: {c.total_pages} ({c.page_category}) | 置信度: {c.confidence:.0%}")
        print(f"信号: {c.signals}")
