# -*- coding: utf-8 -*-
"""
Prompt 管理器 — 从 YAML 构建系统 Prompt
RQagentPro 风格：YAML → 动态拼装 → 变量替换
"""
import os
from typing import Dict, Optional, Any


# T8 (A2) 可用指标知识块 — 体积控制
# 同义词全表（field_dict 256 + kg_resolver 77 条）不整表灌入 prompt：
# 只输出"收敛后的标准指标清单 + 精选归一示例 + 概念组展开"三类派生视图，
# 并受 METRIC_KNOWLEDGE_MAX_CHARS 硬上限约束（默认 1200 字符，可调）。
METRIC_KNOWLEDGE_MAX_CHARS = 1200


class PromptManager:
    """从 config/prompts/ 读取 YAML 并构建系统 Prompt"""

    def __init__(self, config_dir: str = None):
        if config_dir is None:
            config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
        self.config_dir = config_dir
        self._system_config: Optional[Dict] = None
        self._tool_guidance: Optional[Dict] = None

    def _load_yaml(self, filename: str) -> Dict[str, Any]:
        """加载 YAML 文件（无依赖）"""
        import yaml
        filepath = os.path.join(self.config_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(f"[PromptManager] 警告: 文件未找到 {filepath}，使用默认配置")
            return {}
        except Exception as e:
            print(f"[PromptManager] 错误: 加载 {filepath} 失败: {e}")
            return {}

    def load(self):
        """加载所有 prompt 配置文件"""
        self._system_config = self._load_yaml("prompts/system.yaml")

    def _get_config(self) -> Dict:
        """懒加载配置"""
        if self._system_config is None:
            self.load()
        return self._system_config or {}

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持点号分隔的路径 (如 'llm.model')"""
        config = self._get_config()
        keys = key.split(".")
        for k in keys:
            if isinstance(config, dict):
                config = config.get(k)
            else:
                return default
            if config is None:
                return default
        return config

    def build_system_prompt(self, tool_descriptions: str = "",
                            extra_context: str = "",
                            include_metric_knowledge: bool = True,
                            metric_knowledge_max_chars: Optional[int] = None) -> str:
        """
        构建完整的系统 Prompt

        参数:
          tool_descriptions: 工具的简要描述列表（由 ToolRegistry 生成）
          extra_context: 额外的上下文（如上一轮的会话摘要）
          include_metric_knowledge: 是否注入"可用指标知识"块（T8 A2，默认开）
          metric_knowledge_max_chars: 指标知识块体积上限（None 用模块常量）
        """
        knowledge_block = ""
        if include_metric_knowledge:
            knowledge_block = self.build_metric_knowledge_block(
                max_chars=metric_knowledge_max_chars)

        config = self._get_config()
        if not config:
            return self._build_fallback_prompt(
                tool_descriptions, extra_context,
                knowledge_block=knowledge_block,
            )

        parts = []

        # 1. 角色定义
        role = config.get("role", "财务分析助手")
        name = config.get("name", "财务AI")
        parts.append(f"你是{name}，一个专业的{role}。")

        # 2. 核心能力
        capability = config.get("core_capability", "")
        if capability:
            parts.append(capability.strip())

        # 3. 工具说明
        if tool_descriptions:
            parts.append("\n## 可用工具\n")
            parts.append(tool_descriptions)

        # 4. 工具详细指引
        guidance = config.get("tool_guidance", {})
        if guidance:
            parts.append("\n## 工具使用指引\n")
            for tool_name, guide_text in guidance.items():
                guide_text = guide_text.strip()
                parts.append(f"### {tool_name}\n{guide_text}\n")

        # 5. 核心原则
        principles = config.get("principles", [])
        if principles:
            parts.append("## 核心原则\n")
            sorted_principles = sorted(principles, key=lambda p: p.get("priority", 99))
            for p in sorted_principles:
                parts.append(f"{p['priority']}. {p['text']}")

        # 6. 输出格式
        output_format = config.get("output_format", "")
        if output_format:
            parts.append(f"\n## 输出格式\n{output_format.strip()}")

        # 7. 禁止事项
        forbidden = config.get("forbidden", [])
        if forbidden:
            parts.append("\n## 严格禁止\n")
            for i, item in enumerate(forbidden, 1):
                parts.append(f"- {item}")

        # 8. 可用指标知识（静态领域知识，数据来自 kg_resolver/field_dict）
        if knowledge_block:
            parts.append(knowledge_block)

        # 9. 额外上下文（如会话继承信息）
        if extra_context:
            parts.append(f"\n## 当前会话上下文\n{extra_context}")

        return "\n\n".join(parts)

    def _build_fallback_prompt(self, tool_descriptions: str,
                                extra_context: str,
                                knowledge_block: str = "") -> str:
        """兜底 Prompt（YAML 不可用时）"""
        parts = [
            "你是中医药企业财务分析助手。",
            "按以下原则工作：",
            "1. 精确优先：优先查数据库获取精确财务数字，不要编造",
            "2. 单位正确：金额用亿元，比率用百分比，每股指标用元/股",
            "3. 上下文感知：追问时继承上一轮的公司/年份/指标",
        ]
        if tool_descriptions:
            parts.append(f"\n可用工具：\n{tool_descriptions}")
        if knowledge_block:
            parts.append(knowledge_block)
        if extra_context:
            parts.append(f"\n会话上下文：\n{extra_context}")
        return "\n".join(parts)

    def build_metric_knowledge_block(
            self, max_chars: Optional[int] = None) -> str:
        """构建"可用指标知识"上下文块（T8 A2）。

        数据只来自 kg_resolver/field_dict 的只读导出视图，不手抄；
        体积受 max_chars 控制：指标清单与归一示例优先保留，概念组按
        HIERARCHY 顺序从前往后截断，最后整体硬截断兜底 —— 返回值恒不
        超过 max_chars。任何异常都降级为空串，不阻塞 prompt 构建。
        """
        if max_chars is None:
            max_chars = METRIC_KNOWLEDGE_MAX_CHARS
        if not max_chars or max_chars <= 0:
            return ""

        try:
            from agent import kg_resolver
            labels = kg_resolver.standard_indicator_labels()
            norm_pairs = kg_resolver.synonym_norm_examples(limit=8)
            groups = kg_resolver.hierarchy_expansions()
        except Exception as exc:  # 知识块降级为空，不拖垮 prompt 构建
            print(f"[PromptManager] 构建指标知识块失败，已跳过: {exc}")
            return ""

        lines = [
            "## 可用指标知识",
            "sql_query 等工具会把口语/别名自动归一到下面的标准指标名；"
            "用户问概念组词（如“盈利能力”）时工具不会自动展开全组，"
            "请按下述“概念组展开”列表将其拆成具体子指标名称逐一查询。"
            "不要用清单以外的指标名。",
        ]
        if labels:
            lines.append(
                "可用指标清单（共%d个）：%s"
                % (len(labels), "、".join(labels))
            )
        if norm_pairs:
            lines.append(
                "归一示例（口语/别名→标准指标）：%s。"
                % "；".join("%s→%s" % (alias, name) for alias, name in norm_pairs)
            )
        if groups:
            lines.append("概念组展开（用户问概念组时查其全部子指标）：")

        prefix = "\n".join(lines)
        group_lines = [
            "%s→[%s]" % (name, "、".join(children))
            for name, children in groups
        ]

        body = prefix
        if groups:
            remaining = max_chars - len(prefix)
            if remaining >= 0:
                kept = []
                for group_line in group_lines:
                    cost = len(group_line) + 1
                    if remaining >= cost:
                        kept.append(group_line)
                        remaining -= cost
                    else:
                        break
                if kept:
                    body = prefix + "\n" + "\n".join(kept)
                if len(kept) < len(group_lines):
                    marker = "\n…（其余概念组按同一规则展开）"
                    if len(body) + len(marker) <= max_chars:
                        body += marker

        if len(body) > max_chars:
            body = body[:max_chars - 1].rstrip() + "…"
        return body

    def build_context_hint(self, last_company: str = "",
                           last_indicator: str = "",
                           last_year: str = "",
                           last_answer_summary: str = "") -> str:
        """
        构建上下文提示（注入到用户消息中，替代 FollowupResolver 的关键词匹配）

        格式："上一轮查询了{公司}{年份}的{指标}，结果是{摘要}。
               用户的问题可能是对此的追问，请继承相关信息。"
        """
        if not any([last_company, last_indicator, last_year]):
            return ""

        parts = ["[上下文提示：上一轮对话中，"]

        entities = []
        if last_company:
            entities.append(f"关注的公司是「{last_company}」")
        if last_indicator:
            entities.append(f"关注的指标是「{last_indicator}」")
        if last_year:
            entities.append(f"时间范围是「{last_year}」")

        parts.append("，".join(entities))

        if last_answer_summary:
            parts.append(f"。上一轮的回答摘要：{last_answer_summary[:200]}")

        parts.append("。当前用户的问题可能是对此的追问，请自动继承上述信息，"
                      "无需再次询问公司/年份/指标。]")

        return "".join(parts)


# 模块级默认实例
_prompt_manager: Optional[PromptManager] = None


def get_prompt_manager() -> PromptManager:
    """获取 PromptManager 单例"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager
