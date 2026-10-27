# -*- coding: utf-8 -*-
"""工具基类 — RQagentPro 风格: async execute + JSON Schema + 优先级"""
import json
from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseTool(ABC):
    """
    Agent 工具基类

    每个工具必须定义:
      - name: 工具名（LLM 调用时使用）
      - description: 自然语言描述（LLM 理解工具用途）
      - parameters: OpenAI Function Calling 兼容的 JSON Schema
      - priority: 优先级（越高越靠前，也影响 tool choice 偏好）

    必须实现:
      - async execute(**kwargs) -> str: 异步执行，返回 JSON 字符串

    保留兼容:
      - run(inputs: dict) -> dict: 同步包装器（旧代码兼容）
    """

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    priority: int = 50  # 默认优先级

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """
        异步执行工具，返回 JSON 字符串

        参数由 LLM 根据 parameters schema 传入（keyword arguments）
        返回必须是有效的 JSON 字符串，包含至少 success 字段
        """
        ...

    def run(self, inputs: dict) -> dict:
        """
        同步包装器 — 兼容旧版 FinanceAgent._execute_plan()

        默认实现：将 inputs dict 转为 kwargs 调用 execute()，
        然后解析 JSON 返回 dict。

        子类可以覆盖此方法提供更高效的同步实现。
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 已有事件循环在运行，创建新的
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run, self.execute(**inputs)
                    )
                    result_json = future.result(timeout=30)
            else:
                result_json = asyncio.run(self.execute(**inputs))
        except RuntimeError:
            result_json = asyncio.run(self.execute(**inputs))

        try:
            return json.loads(result_json)
        except json.JSONDecodeError:
            return {"success": False, "error": "工具返回了无效JSON", "raw": result_json}

    def to_openai_schema(self) -> dict:
        """生成 OpenAI Function Calling 兼容的工具 Schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description.strip(),
                "parameters": self.parameters,
            }
        }

    def to_schema(self) -> dict:
        """返回工具的简要信息（旧版兼容）"""
        return {
            "name": self.name,
            "description": self.description,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name}, priority={self.priority})>"
