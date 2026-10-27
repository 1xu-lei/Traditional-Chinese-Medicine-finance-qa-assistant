# -*- coding: utf-8 -*-
"""
NER 工具 — 包装自训练的 BERT NER 模型
作为 Agent 的可选工具（非强制预处理）
"""
import json
import sys
import os

from agent.tools.base import BaseTool


class NERTool(BaseTool):
    """
    命名实体识别工具 — 使用自训练的 BertForTokenClassification 模型
    从财务问题中提取实体槽位: COMP(公司)、METRIC(指标)、PERIOD(报告期)

    作为 Agent 的可选工具而不是强制预处理，
    LLM 自主决定是否需要调用。
    """

    name = "ner_extract"
    description = """使用自训练的 BERT 命名实体识别模型，从财务问题中提取实体槽位。

返回:
  - COMP: 公司名列表
  - METRIC: 财务指标名列表
  - PERIOD: 报告期列表

适用场景: 用户表达模糊、需要精确提取实体时。
不适用: 问题中的公司名和指标名已经很明确时，直接调 sql_query 即可。"""

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

    priority = 60

    def __init__(self):
        self._extractor = None

    def _get_extractor(self):
        if self._extractor is None:
            sys.path.insert(0, os.path.join(
                os.path.dirname(__file__), "..", "..",
                "models", "model2_question_understanding"
            ))
            from ner_inference import NERExtractor
            self._extractor = NERExtractor()
        return self._extractor

    async def execute(self, question: str = "") -> str:
        try:
            extractor = self._get_extractor()
            slots = extractor.extract_slots(question) if extractor.model else {}
            return json.dumps(slots, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "error": str(e),
                "COMP": [], "METRIC": [], "PERIOD": [],
            }, ensure_ascii=False)

    def run(self, inputs: dict) -> dict:
        """同步包装器"""
        question = inputs.get("question", "")
        import asyncio
        result = asyncio.run(self.execute(question=question))
        return json.loads(result)
