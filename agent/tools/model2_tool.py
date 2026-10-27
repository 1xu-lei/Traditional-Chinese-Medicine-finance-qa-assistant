# -*- coding: utf-8 -*-
"""
Model2 工具 — 包装自训练的 BART 意图分类模型
作为 Agent 的可选工具（非强制预处理）
"""
import json
import sys
import os

from agent.tools.base import BaseTool


class Model2Tool(BaseTool):
    """
    意图分类工具 — 使用自训练的 Bart-base-chinese 模型
    判断用户查询意图类型并提取关键信息

    作为 Agent 的可选工具而不是强制预处理，
    LLM 自主决定是否需要调用。
    """

    name = "model2_understand"
    description = """使用自训练的 BART 意图分类模型，判断用户意图并提取关键信息。

返回意图类型:
  - basic_query: 基础查询（单公司单指标）
  - time_trend: 时间趋势（近N年变化）
  - comparison: 对比分析（多公司比较）
  - stat_query: 统计排名（Top N）
  - analysis_query: 分析类（需要研报支持）
  - fuzzy_intent: 模糊意图
  - report: 生成报告
  - open_question: 开放性问题

还会提取: company(公司名)、indicator(指标)、year(年份)、period(报告期)、top_k(排名数)

适用场景: 问题模糊/歧义时需要意图分类辅助判断。
不适用: 问题意图明确（如直接说"净利润"）时，跳过此工具直接调 sql_query。"""

    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "需要分析的问题文本",
            },
        },
        "required": ["question"],
    }

    priority = 55

    def __init__(self):
        self._model = None

    def _get_model(self):
        if self._model is None:
            sys.path.insert(0, os.path.join(
                os.path.dirname(__file__), "..", "..",
                "models", "model2_question_understanding"
            ))
            from inference_model2 import QuestionUnderstandingModel
            self._model = QuestionUnderstandingModel()
        return self._model

    async def execute(self, question: str = "") -> str:
        try:
            model = self._get_model()
            result = model.understand(question)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "error": str(e),
                "intent": "basic_query",
            }, ensure_ascii=False)

    def run(self, inputs: dict) -> dict:
        """同步包装器"""
        question = inputs.get("question", "")
        import asyncio
        result = asyncio.run(self.execute(question=question))
        return json.loads(result)
