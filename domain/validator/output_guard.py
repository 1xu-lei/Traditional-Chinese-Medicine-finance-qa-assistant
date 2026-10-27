# -*- coding: utf-8 -*-
"""
财务输出校验器 — Agent 生成回答后的质量检查
RQagentPro 风格：禁止模式 + 数字合理性 + 引用可追溯
"""
import re
from typing import Dict, List, Tuple


class OutputGuard:
    """财务输出质量校验"""

    # ---- 禁止模式 ----

    FORBIDDEN_PATTERNS: List[Tuple[str, str]] = [
        # (正则模式, 违规说明)
        (r"我(?:不)?知道", "不应使用'我不知道'"),
        (r"我猜[测想]?", "不应使用主观猜测用语"),
        (r"我(?:个人)?觉得", "不应使用'我觉得'"),
        (r"大概[是大约有]?", "不应使用不确定用语'大概'"),
        (r"可能[是有]?", "不应使用不确定用语'可能'"),
        (r"也许", "不应使用不确定用语'也许'"),
        (r"根据我的知识", "应基于查询结果而非模型知识"),
        (r"I don'?t know", "不应使用英文'我不知道'"),
    ]

    # ---- 数字-单位一致性模式 ----

    # 比率字段不应该带金额单位
    RATIO_WITH_AMOUNT_UNIT = re.compile(
        r'(毛利率|净利率|资产负债率|净资产收益率|ROE|占比|增长率|增速|同比|环比)'
        r'.{0,10}[\d.,]+\s*(?:亿|万)\s*(?:元|块钱|人民币)'
    )

    # 金额字段不应该带 %
    AMOUNT_WITH_PERCENT = re.compile(
        r'(营业总收入|营业收入|营收|净利润|总资产|负债|现金流|利润|成本|费用)'
        r'.{0,20}[\d.,]+\s*%'
    )

    # ---- 合理性阈值 ----

    RATIO_LIMITS = {
        "毛利率": (-100.0, 100.0),    # 极端情况可负可超100
        "净利率": (-100.0, 100.0),
        "资产负债率": (0.0, 200.0),
        "ROE": (-200.0, 200.0),
        "增长率": (-1000.0, 1000.0),
    }

    # ---- 主校验方法 ----

    def validate(self, answer: str, tool_calls_log: List[Dict] = None) -> Dict:
        """
        校验 Agent 输出

        参数:
          answer: Agent 生成的回答文本
          tool_calls_log: 工具调用日志（用于校验数据可追溯）

        返回:
          {
            "passed": bool,
            "issues": [{"severity": "error"|"warning", "message": str}],
            "score": float  (0.0 ~ 1.0)
          }
        """
        issues = []

        # 1. 空回答检查
        if not answer or len(answer.strip()) < 10:
            issues.append({
                "severity": "error",
                "message": f"回答过短 ({len(answer) if answer else 0} 字符)",
            })

        # 2. 过长回答
        if len(answer) > 3000:
            issues.append({
                "severity": "warning",
                "message": f"回答过长 ({len(answer)} 字符)，可能需要精简",
            })

        # 3. 禁止模式检测
        for pattern, desc in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, answer):
                issues.append({
                    "severity": "warning",
                    "message": f"{desc}: 匹配到 '{pattern}'",
                })

        # 4. 单位一致性检查
        ratio_with_unit = self.RATIO_WITH_AMOUNT_UNIT.search(answer)
        if ratio_with_unit:
            issues.append({
                "severity": "error",
                "message": f"比率字段不应带金额单位: '{ratio_with_unit.group()}'",
            })

        amount_with_pct = self.AMOUNT_WITH_PERCENT.search(answer)
        if amount_with_pct:
            issues.append({
                "severity": "error",
                "message": f"金额字段不应带百分比: '{amount_with_pct.group()}'",
            })

        # 5. 数字合理性粗略检查
        # 提取所有"数字+单位"对
        numbers_with_units = re.findall(
            r'([\d.,]+)\s*(亿元|万元|元|%|元/股)', answer
        )
        for num_str, unit in numbers_with_units:
            try:
                num = float(num_str.replace(",", ""))
            except ValueError:
                continue
            if unit == "%" and abs(num) > 1000:
                issues.append({
                    "severity": "warning",
                    "message": f"百分比值 {num}% 可能异常（超过1000%）",
                })
            if unit == "亿元" and abs(num) > 100000:
                issues.append({
                    "severity": "warning",
                    "message": f"金额 {num}亿元 可能异常（超过10万亿）",
                })

        # 6. 是否有数据引用（是否真的查了东西）
        if tool_calls_log and not any(
            tc.get("tool") == "sql_query" and tc.get("result_preview", "").find('"success":true') >= 0
            for tc in tool_calls_log
        ):
            # 没查过数据库但回答了具体数字 → 可能编造
            if re.search(r'[\d.]+\s*(?:亿元|万元|%)', answer):
                issues.append({
                    "severity": "warning",
                    "message": "回答包含具体数字但未查到 SQL 数据来源，可能编造",
                })

        # 计算分数
        errors = sum(1 for i in issues if i["severity"] == "error")
        warnings = sum(1 for i in issues if i["severity"] == "warning")
        score = max(0.0, 1.0 - errors * 0.3 - warnings * 0.1)

        return {
            "passed": errors == 0,
            "issues": issues,
            "score": round(score, 2),
            "total_issues": len(issues),
        }

    def validate_quick(self, answer: str) -> bool:
        """快速检查：是否通过了最基本的校验"""
        result = self.validate(answer)
        return result["passed"]


# 模块级默认实例
_output_guard: OutputGuard = None


def get_output_guard() -> OutputGuard:
    """获取 OutputGuard 单例"""
    global _output_guard
    if _output_guard is None:
        _output_guard = OutputGuard()
    return _output_guard
