# -*- coding: utf-8 -*-
"""
工具注册中心 — 集中管理所有 Agent 工具
RQagentPro 风格：register / unregister / OpenAI Schema 生成 / 单例
"""
from typing import Dict, List, Optional, Type
from agent.tools.base import BaseTool


class ToolRegistry:
    """工具注册中心：统一管理工具的注册、查找、Schema生成"""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具"""
        if tool.name in self._tools:
            print(f"[ToolRegistry] 警告: 工具 '{tool.name}' 已注册，将被覆盖")
        self._tools[tool.name] = tool

    def register_all(self, tools: List[BaseTool]) -> None:
        """批量注册工具"""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        """移除一个工具"""
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[BaseTool]:
        """按名称获取工具"""
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """列出所有已注册工具名"""
        return list(self._tools.keys())

    def list_by_priority(self) -> List[BaseTool]:
        """按优先级排序的工具列表（高优先级排前面）"""
        return sorted(self._tools.values(), key=lambda t: t.priority, reverse=True)

    def to_openai_schemas(self) -> List[dict]:
        """
        生成 OpenAI Function Calling 兼容的工具 Schema 列表
        按优先级排序
        """
        schemas = []
        for tool in self.list_by_priority():
            schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description.strip(),
                    "parameters": tool.parameters,
                }
            }
            schemas.append(schema)
        return schemas

    def get_tool_descriptions(self) -> str:
        """生成人类可读的工具描述（用于系统 Prompt）"""
        lines = []
        for tool in self.list_by_priority():
            desc = tool.description.strip().split("\n")[0][:100]
            lines.append(f"- **{tool.name}**: {desc}")
        return "\n".join(lines)

    async def execute(self, name: str, **kwargs) -> str:
        """执行指定工具，返回 JSON 字符串"""
        tool = self._tools.get(name)
        if not tool:
            return '{"error": "工具未找到: ' + name + '"}'
        try:
            result = await tool.execute(**kwargs)
            return result
        except Exception as e:
            return '{"error": "' + str(e).replace('"', "'") + '"}'

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# 全局单例
_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """获取全局工具注册中心单例"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
