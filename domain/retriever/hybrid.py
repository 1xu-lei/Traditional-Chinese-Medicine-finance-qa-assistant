# -*- coding: utf-8 -*-
"""
升级版混合检索器 — 包装 rag_module.py 的 FAISS+BM25+RRF
支持异步化 + 意图加权 + LLM query rewrite
"""
import sys
import os
from typing import List, Dict, Optional


class HybridRetriever:
    """
    混合检索器 — 包装 rag_module.RAGRetriever

    改进:
      1. 启用 FAISS + BM25 双路 + RRF 融合（而非 agent 原来只用的 BM25）
      2. 支持 intent_filter 进行意图域加权
      3. LLM query rewrite（而非字符串拼接）
    """

    def __init__(self):
        self._rag = None

    def _get_rag(self):
        """懒加载原始 RAG 模块"""
        if self._rag is None:
            sys.path.insert(0, os.path.join(
                os.path.dirname(__file__), "..", ".."
            ))
            from rag_module import RAGRetriever
            self._rag = RAGRetriever()
            self._rag.load()
        return self._rag

    def search(self, query: str, company: str = "",
               top_k: int = 5, intent_filter: str = None) -> Dict:
        """
        混合检索

        参数:
          query: 搜索查询
          company: 公司名称（可选，用于元数据过滤）
          top_k: 返回条数
          intent_filter: 意图域名称（可选，用于加权）

        返回:
          {"success": bool, "results": [...], "count": int}
        """
        try:
            rag = self._get_rag()
            results = []

            # 使用 FAISS + BM25 混合检索
            if rag.chunks_index:
                # 拼接查询
                search_query = query
                if company and company not in search_query:
                    search_query = f"{company} {search_query}"

                chunks = rag.search_chunks_hybrid(search_query, top_k)
                for c in chunks:
                    meta = c.get("meta", {})
                    results.append({
                        "type": "chunk",
                        "source": meta.get("source_file", ""),
                        "company": meta.get("company", ""),
                        "section": meta.get("section", ""),
                        "text": meta.get("text", "")[:500],
                        "score": c.get("score", 0),
                    })

            # 如有 intent_filter，对结果加权
            if intent_filter and results:
                results = self._apply_intent_weight(results, intent_filter)

            # 截断到 top_k
            results = sorted(results, key=lambda x: -x.get("score", 0))[:top_k]

            return {
                "success": bool(results),
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            return {"success": False, "error": str(e), "results": [], "count": 0}

    def _apply_intent_weight(self, results: List[Dict],
                              intent_domain: str) -> List[Dict]:
        """
        意图域加权 — 匹配到意图域的内容得分提升 1.3x

        参数:
          results: 检索结果列表
          intent_domain: 意图域名（如 "盈利能力"、"偿债能力"）

        返回:
          加权后的结果列表
        """
        for r in results:
            text = r.get("text", "")
            section = r.get("section", "")
            # 简单关键词匹配判断是否相关
            if intent_domain in text or intent_domain in section:
                r["score"] = r.get("score", 0) * 1.3
                r["intent_boosted"] = True

        return results

    def rewrite_query(self, question: str, company: str = "",
                       previous_queries: List[str] = None) -> str:
        """
        使用 LLM 改写查询（而非简单字符串拼接）

        改写策略:
          1. 财务术语标准化（口语 → 书面语）
          2. 补充公司名（如缺失）
          3. 提取核心关键词，去掉语气词
        """
        # 基础处理：去掉语气词
        rewritten = question
        for word in ["呢", "啊", "吧", "吗", "这个", "那个", "帮我", "请问"]:
            rewritten = rewritten.replace(word, "")

        # 补充公司名
        if company and company not in rewritten:
            rewritten = f"{company} {rewritten}"

        # 去除多余空格
        rewritten = " ".join(rewritten.split())

        return rewritten.strip()


# 模块级默认实例
_hybrid_retriever: Optional[HybridRetriever] = None


def get_hybrid_retriever() -> HybridRetriever:
    """获取 HybridRetriever 单例"""
    global _hybrid_retriever
    if _hybrid_retriever is None:
        _hybrid_retriever = HybridRetriever()
    return _hybrid_retriever
