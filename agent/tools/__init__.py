# -*- coding: utf-8 -*-
"""工具模块 — 注册所有 Agent 工具"""
from agent.tools.registry import get_tool_registry


def register_all_tools():
    """注册所有工具到全局注册中心（优先级从高到低）"""
    from agent.tools.sql_tool import SQLTool
    from agent.tools.rag_tool import RAGTool
    from agent.tools.chart_tool import ChartTool
    from agent.tools.data_tool import DataTool
    from agent.tools.ner_tool import NERTool
    from agent.tools.model2_tool import Model2Tool
    from agent.tools.risk_tool import RiskTool
    from agent.tools.report_tool import ReportTool

    registry = get_tool_registry()
    registry.register_all([
        SQLTool(),          # priority 100 — 查数据第一优先级
        RAGTool(),          # priority 80  — 研报搜索
        ChartTool(),        # priority 70  — 图表
        DataTool(),         # priority 65  — 数据计算
        NERTool(),          # priority 60  — 自训练 NER 模型
        Model2Tool(),       # priority 55  — 自训练意图分类模型
        RiskTool(),         # priority 50  — 风险预警
        ReportTool(),       # priority 40  — 报告生成
    ])

    print(f"[Tools] 已注册 {len(registry)} 个工具: {registry.list_tools()}")
    return registry
