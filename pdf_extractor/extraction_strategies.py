# -*- coding: utf-8 -*-
"""
extraction_strategies.py: 基于 PDF 分类的类型化提取策略

核心理念（2026-08-08 重构）：
  不同 PDF 类型有不同排版结构。先分类，再选择策略，不再一套逻辑硬套。

策略分类:
  - StrategyFYFull:    年报完整版（150+页，合并+母公司双表，三表跨多页）
  - StrategyFYSummary: 年报摘要版（2-5页，仅CPI表）
  - StrategyH1Full:    半年报完整版（80-130页，仅合并表，H1累计口径）
  - StrategyQuarterly: 季报（10-80页，仅合并表，Q1/Q3累计口径）
  - StrategyGeneric:   兜底策略（保守模式，全扫描+评分回退）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from pdf_extractor.pdf_classifier import PDFClassification


# ============================================================
# 策略配置
# ============================================================

@dataclass
class ExtractionConfig:
    """提取策略的配置参数"""
    # 三表配置
    extract_income: bool = True
    extract_balance: bool = True
    extract_cashflow: bool = True
    extract_cpi: bool = True

    # 扫描范围
    max_pages: Optional[int] = None      # None=全扫
    scan_step: int = 1                   # 跳页扫描步长（1=逐页）
    section_search_radius: int = 5       # section标题前后搜索半径（页）

    # 跨页合并
    cross_page_merge: bool = True        # 资产负债表/现金流量表跨页合并
    cross_page_max_gap: int = 3          # 跨页最大间隙

    # Variant 处理
    prefer_consolidated: bool = True     # 合并表优先
    extract_parent: bool = False         # 是否同时提取母公司表

    # 单位检测
    unit_scan_method: str = "global"     # "global" | "table_local" | "both"
    unit_fallback_factor: float = 1.0    # 兜底倍率（1=万元）

    # 评分回退
    enable_score_fallback: bool = True   # 分块失败后是否评分回退
    score_threshold: int = 60            # 回退最低分

    # 校验
    enable_m7_monotonic: bool = True     # M7 累计单调性
    enable_identity_check: bool = True   # 恒等式校验
    min_fields_per_table: Dict[str, int] = field(default_factory=lambda: {
        "income_sheet": 4,
        "balance_sheet": 3,
        "cash_flow_sheet": 3,
        "core_performance_indicators_sheet": 2,
    })

    # 日志
    verbose: bool = True
    log_every_page: bool = False


# ============================================================
# 策略工厂
# ============================================================

def get_strategy(classification: PDFClassification, filename: str = "") -> ExtractionConfig:
    """
    根据 PDF 分类结果返回对应的提取策略

    参数:
      classification: PDFClassifier.classify() 的结果
      filename: PDF 文件名（用于兜底判断）

    返回:
      ExtractionConfig 策略配置
    """
    rt = classification.report_type      # FY | H1 | Q1 | Q3
    rf = classification.report_format    # full | summary
    sv = classification.sheet_variant    # consolidated_only | both | parent_only
    pc = classification.page_category    # tiny | medium | large | xlarge

    # === 摘要版：跳过三主表，只提取 CPI ===
    if rf == "summary":
        cfg = ExtractionConfig(
            extract_income=False,
            extract_balance=False,
            extract_cashflow=False,
            extract_cpi=True,
            max_pages=None,                # 摘要版总共就几页，全扫
            cross_page_merge=False,         # 不需要跨页
            enable_score_fallback=False,    # 摘要版不触发评分回退
            prefer_consolidated=True,
            extract_parent=False,
            unit_scan_method="global",     # 摘要版全局扫描单位
            unit_fallback_factor=1.0,
        )
        _log_strategy("FY Summary", filename, cfg, classification)
        return cfg

    # === 年报完整版 ===
    if rt == "FY" and rf == "full":
        cfg = ExtractionConfig(
            extract_income=True,
            extract_balance=True,
            extract_cashflow=True,
            extract_cpi=True,
            max_pages=None,                # 全扫
            scan_step=1,                   # 逐页（页多但每页都要看）
            section_search_radius=8,       # 大报告，搜索半径大
            cross_page_merge=True,
            cross_page_max_gap=3,          # 资产负债表跨2-3页
            prefer_consolidated=True,
            extract_parent=(sv == "both"), # 有母公司表才提取
            unit_scan_method="global",     # 先全局扫，表级复核
            unit_fallback_factor=1.0,
            enable_score_fallback=True,
            score_threshold=60,
        )
        _log_strategy("FY Full", filename, cfg, classification)
        return cfg

    # === 半年报完整版 ===
    if rt == "H1" and rf == "full":
        cfg = ExtractionConfig(
            extract_income=True,
            extract_balance=True,
            extract_cashflow=True,
            extract_cpi=True,
            max_pages=None,
            scan_step=1,
            section_search_radius=6,       # 半年报比年报薄
            cross_page_merge=True,
            cross_page_max_gap=2,          # 通常跨1-2页
            prefer_consolidated=True,
            extract_parent=False,          # 半年报通常只有合并表
            unit_scan_method="global",
            unit_fallback_factor=1.0,
            enable_score_fallback=True,
            score_threshold=60,
        )
        _log_strategy("H1 Full", filename, cfg, classification)
        return cfg

    # === 季报（Q1/Q3）===
    if rt in ("Q1", "Q3") and rf == "full":
        cfg = ExtractionConfig(
            extract_income=True,
            extract_balance=True,
            extract_cashflow=True,
            extract_cpi=True,
            max_pages=None,                # 季报总共~10-80页
            scan_step=1,
            section_search_radius=3,       # 季报更紧凑
            cross_page_merge=True,
            cross_page_max_gap=1,          # 季报很少跨2页以上
            prefer_consolidated=True,
            extract_parent=False,          # 季报只有合并表
            unit_scan_method="global",
            unit_fallback_factor=1.0,
            enable_score_fallback=True,
            score_threshold=55,            # 季报表格可能更简略，降低阈值
        )
        _log_strategy("Quarterly", filename, cfg, classification)
        return cfg

    # === 兜底：保守模式 ===
    cfg = ExtractionConfig(
        extract_income=True,
        extract_balance=True,
        extract_cashflow=True,
        extract_cpi=True,
        max_pages=None,
        scan_step=1,
        section_search_radius=5,
        cross_page_merge=True,
        cross_page_max_gap=3,
        prefer_consolidated=True,
        extract_parent=False,
        unit_scan_method="global",
        unit_fallback_factor=1.0,
        enable_score_fallback=True,
        score_threshold=60,
    )
    _log_strategy("Generic(Fallback)", filename, cfg, classification)
    return cfg


def _log_strategy(name, filename, cfg, classification):
    """打印策略选择日志"""
    fn_short = filename[:50] if filename else "?"
    print(f"  [策略] {name} → {fn_short}")
    print(f"         类型={classification.report_type} "
          f"格式={classification.report_format} "
          f"variant={classification.sheet_variant} "
          f"页数={classification.total_pages}({classification.page_category})")
    if cfg.extract_parent:
        print(f"         三表提取: income+balance+cashflow+CPI | 含母公司表")
    elif not cfg.extract_income:
        print(f"         三表提取: 仅CPI | 跳过三主表（摘要版）")
    else:
        print(f"         三表提取: income+balance+cashflow+CPI | 仅合并表")


# ============================================================
# 策略建议（供 table_extractor 使用）
# ============================================================

def should_skip_table(cfg: ExtractionConfig, table_type: str) -> bool:
    """判断是否应该跳过某张表的提取"""
    skip_map = {
        "income_sheet": not cfg.extract_income,
        "balance_sheet": not cfg.extract_balance,
        "cash_flow_sheet": not cfg.extract_cashflow,
        "core_performance_indicators_sheet": not cfg.extract_cpi,
    }
    return skip_map.get(table_type, False)


def get_cross_page_params(cfg: ExtractionConfig) -> Tuple[bool, int]:
    """返回跨页合并参数 (是否合并, 最大间隙)"""
    return cfg.cross_page_merge, cfg.cross_page_max_gap


def get_score_thresholds(cfg: ExtractionConfig, table_type: str) -> int:
    """返回评分阈值（可被 table_extractor 的 TABLE_PROFILES 覆盖）"""
    return cfg.score_threshold


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    from pdf_extractor.pdf_classifier import classify_pdf
    import os

    test_files = [
        r"data\附件2：财务报告\reports-深交所\东阿阿胶：2022年年度报告.pdf",
        r"data\附件2：财务报告\reports-深交所\东阿阿胶：2022年年度报告摘要.pdf",
        r"data\附件2：财务报告\reports-深交所\东阿阿胶：2023年半年度报告.pdf",
        r"data\附件2：财务报告\reports-深交所\东阿阿胶：2023年一季度报告.pdf",
        r"data\附件2：财务报告\reports-上交所\600085_20230328_GGS9.pdf",
        r"data\附件2：财务报告\reports-上交所\600252_20230329_84OV.pdf",
    ]

    for fp in test_files:
        if not os.path.exists(fp):
            continue
        c = classify_pdf(fp)
        cfg = get_strategy(c, os.path.basename(fp))
        print(f"\n{'='*60}")
        print(f"文件: {os.path.basename(fp)[:50]}")
        print(f"分类: type={c.report_type} format={c.report_format} "
              f"variant={c.sheet_variant} pages={c.total_pages}")
        print(f"策略: income={cfg.extract_income} balance={cfg.extract_balance} "
              f"cashflow={cfg.extract_cashflow} cpi={cfg.extract_cpi}")
        print(f"      合并={cfg.prefer_consolidated} 母公司={cfg.extract_parent} "
              f"跨页={cfg.cross_page_merge}(max_gap={cfg.cross_page_max_gap})")
