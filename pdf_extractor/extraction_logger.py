# -*- coding: utf-8 -*-
"""
提取管线日志系统 — 记录每次 PDF 提取的全过程
输出到 data/logs/extraction/{stock_code}_{year}_{period}.json

每条日志包含:
  - 来源 PDF 路径 + 页码
  - 每个表格的提取评分、方式、是否成功
  - 每个字段的: 原始标签 → 映射字段名 → 映射置信度 → 原始值 → 归一化值 → 单位因子
  - 所有 validator 校验项通过/失败明细
  - 整体成功率统计
"""
import json
import os
import time
from typing import Dict, List, Optional


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "data", "logs", "extraction")
os.makedirs(LOG_DIR, exist_ok=True)


class ExtractionLogger:
    """单次 PDF 提取的完整日志记录器"""

    def __init__(self, file_path: str, stock_code: str,
                 report_year: int, report_period: str):
        self.file_path = file_path
        self.stock_code = stock_code
        self.report_year = report_year
        self.report_period = report_period
        self.start_time = time.time()

        self.pdf_run_id = f"{stock_code}_{report_year}_{report_period}"

        # 日志数据结构
        self.log: Dict = {
            "pdf_run_id": self.pdf_run_id,
            "file_path": file_path,
            "stock_code": stock_code,
            "report_year": report_year,
            "report_period": report_period,
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tables": {},        # {"income_sheet": {...}, ...}
            "validation": {},    # validator 校验结果
            "summary": {},       # 最终汇总
        }

        # 计数
        self._field_count = 0
        self._table_count = 0

        print(f"[Log] 开始记录: {self.pdf_run_id}")

    # ================================================================
    # 表格级记录
    # ================================================================

    def record_table(self, table_type: str, found: bool, score: float,
                     page: int, extraction_method: str,
                     unit_factor: float = 10000, unit_raw: str = "万元",
                     source_type: str = "statement"):
        """
        记录一个财务报表的提取结果

        参数:
          table_type: income_sheet / balance_sheet / cash_flow_sheet / core_performance_indicators_sheet
          found: 是否成功提取到数据
          score: 表格评分 (0-100)
          page: 页号 (从 0 开始)
          extraction_method: section_based / fallback_scoring / paragraph
        """
        status = "success" if found else "failed"
        self._table_count += 1 if found else 0

        self.log["tables"][table_type] = {
            "status": status,
            "found": found,
            "score": round(score, 1),
            "page": page,
            "extraction_method": extraction_method,
            "source_type": source_type,
            "unit_factor": unit_factor,
            "unit_raw": unit_raw,
            "fields": [],  # 字段列表（后续由 record_field 填充）
            "field_count": 0,
        }
        print(f"  [Log] {table_type}: {status} score={score:.0f} page={page+1} method={extraction_method}")

    # ================================================================
    # 字段级记录
    # ================================================================

    def record_field(self, table_type: str, raw_label: str,
                     mapped_field: str, confidence: float,
                     raw_value: float, normalized_value: float,
                     unit_factor: float, unit_raw: str):
        """
        记录单个字段的提取细节

        参数:
          table_type: 表类型
          raw_label: PDF 中的原始中文标签 (如 "营业总收入")
          mapped_field: 映射到的英文字段名 (如 "total_operating_revenue")
          confidence: 映射置信度 (0-100)
          raw_value: 从 PDF 表格提取的原始数值
          normalized_value: 归一化后的值 (万元)
          unit_factor: 使用的单位倍率因子
          unit_raw: 原始单位文本
        """
        self._field_count += 1

        field_entry = {
            "raw_label": raw_label,
            "mapped_field": mapped_field,
            "confidence": round(confidence, 1),
            "raw_value": raw_value,
            "normalized_value": normalized_value,
            "unit_factor": unit_factor,
            "unit_raw": unit_raw,
        }

        if table_type in self.log["tables"]:
            self.log["tables"][table_type]["fields"].append(field_entry)
            self.log["tables"][table_type]["field_count"] += 1

    # ================================================================
    # 校验记录
    # ================================================================

    def record_validation(self, pass_count: int, total_count: int,
                          results: List[Dict], score: float):
        """记录 validator 校验结果"""
        self.log["validation"] = {
            "pass_count": pass_count,
            "total_count": total_count,
            "pass_rate": round(pass_count / max(total_count, 1) * 100, 1),
            "score": round(score, 1),
            "details": [
                {
                    "check": r.get("check", ""),
                    "passed": r.get("passed", False),
                    "detail": r.get("detail", ""),
                }
                for r in results
            ],
        }

        failed = [r for r in results if not r.get("passed")]
        if failed:
            print(f"  [Log] 校验: {pass_count}/{total_count} 通过, 失败项: {[r['check'] for r in failed]}")

    # ================================================================
    # 汇总与写入
    # ================================================================

    def finish(self, success: bool, total_fields: int = None):
        """
        结束记录并写入 JSON 文件

        参数:
          success: 本次提取是否成功
          total_fields: 字段总数（跨表合计）
        """
        elapsed = round(time.time() - self.start_time, 2)

        self.log["summary"] = {
            "success": success,
            "elapsed_seconds": elapsed,
            "total_tables": len(self.log["tables"]),
            "tables_found": self._table_count,
            "total_fields": total_fields or self._field_count,
            "table_success_rate": round(
                self._table_count / max(len(self.log["tables"]), 1) * 100, 1
            ),
            "end_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 写 JSON 文件
        out_path = os.path.join(LOG_DIR, f"{self.pdf_run_id}.json")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(self.log, f, ensure_ascii=False, indent=2)
            print(f"  [Log] ✓ 日志已保存: {out_path} ({elapsed}s, {self._field_count} 字段)")
        except Exception as e:
            print(f"  [Log] ✗ 日志写入失败: {e}")

        return out_path

    # ================================================================
    # 便捷方法
    # ================================================================

    def record_field_batch(self, table_type: str, best_per_field: Dict,
                            unit_factor: float, unit_raw: str):
        """
        批量记录字段（从 extract_sse.py 的 best_per_field 格式）

        参数:
          table_type: 表类型
          best_per_field: {field_name: (normalized_value, confidence, raw_label, pdf_raw_value), ...}
          unit_factor: 单位因子
          unit_raw: 单位文本
        """
        for fname, item in best_per_field.items():
            value, conf, orig_label = item[0], item[1], item[2]
            pdf_raw_val = item[3] if len(item) >= 4 else None  # PDF 原始值
            self.record_field(
                table_type=table_type,
                raw_label=orig_label,
                mapped_field=fname,
                confidence=conf,
                raw_value=pdf_raw_val,        # 现在存真正的 PDF 原始值
                normalized_value=value,       # 归一化后的值（万元）
                unit_factor=unit_factor,
                unit_raw=unit_raw,
            )


# ============================================================
# 汇总统计工具
# ============================================================

def load_all_logs() -> List[Dict]:
    """加载所有提取日志"""
    if not os.path.isdir(LOG_DIR):
        return []
    logs = []
    for fn in sorted(os.listdir(LOG_DIR)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(LOG_DIR, fn), encoding="utf-8") as f:
                    logs.append(json.load(f))
            except Exception:
                pass
    return logs


def compute_success_rate(logs: List[Dict]) -> Dict:
    """
    从日志计算汇总成功率

    返回:
      {
        "total_pdfs": int,
        "successful_pdfs": int,
        "pdf_success_rate": float,    # PDF 处理成功率
        "total_fields": int,
        "avg_fields_per_pdf": float,
        "by_year": {year: {...}},
        "by_table": {table: {...}},    # 各表提取成功率
        "field_confidence_avg": float, # 平均字段映射置信度
      }
    """
    if not logs:
        return {"total_pdfs": 0, "message": "无提取日志"}

    total = len(logs)
    successful = sum(1 for l in logs if l.get("summary", {}).get("success"))

    total_fields = sum(l.get("summary", {}).get("total_fields", 0) for l in logs)
    total_tables = sum(l.get("summary", {}).get("total_tables", 0) for l in logs)
    tables_found = sum(l.get("summary", {}).get("tables_found", 0) for l in logs)

    # 字段置信度平均
    confidences = []
    for log in logs:
        for tbl in log.get("tables", {}).values():
            for f in tbl.get("fields", []):
                c = f.get("confidence", 0)
                if c > 0:
                    confidences.append(c)

    # 按年分组
    by_year = {}
    for log in logs:
        yr = str(log.get("report_year", "unknown"))
        if yr not in by_year:
            by_year[yr] = {"total": 0, "success": 0}
        by_year[yr]["total"] += 1
        if log.get("summary", {}).get("success"):
            by_year[yr]["success"] += 1

    for yr in by_year:
        by_year[yr]["rate"] = round(
            by_year[yr]["success"] / max(by_year[yr]["total"], 1) * 100, 1
        )

    # 按表分组
    by_table = {}
    for log in logs:
        for tbl_name, tbl_data in log.get("tables", {}).items():
            if tbl_name not in by_table:
                by_table[tbl_name] = {"total": 0, "found": 0, "total_fields": 0}
            by_table[tbl_name]["total"] += 1
            if tbl_data.get("found"):
                by_table[tbl_name]["found"] += 1
            by_table[tbl_name]["total_fields"] += tbl_data.get("field_count", 0)

    for tbl in by_table:
        by_table[tbl]["rate"] = round(
            by_table[tbl]["found"] / max(by_table[tbl]["total"], 1) * 100, 1
        )

    return {
        "total_pdfs": total,
        "successful_pdfs": successful,
        "pdf_success_rate": round(successful / max(total, 1) * 100, 1),
        "total_fields": total_fields,
        "avg_fields_per_pdf": round(total_fields / max(total, 1), 1),
        "total_tables": total_tables,
        "tables_found": tables_found,
        "table_success_rate": round(tables_found / max(total_tables, 1) * 100, 1),
        "field_confidence_avg": round(sum(confidences) / max(len(confidences), 1), 1),
        "field_confidence_median": _median(confidences) if confidences else 0,
        "by_year": by_year,
        "by_table": by_table,
    }


def _median(values: List[float]) -> float:
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
