# -*- coding: utf-8 -*-
"""RAG搜索工具：封装rag_module"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.tools.base import BaseTool


class RAGTool(BaseTool):
    name = "rag_search"
    description = """搜索研报库中的定性分析内容。用于：行业分析、竞争格局、增长原因、趋势解读。
不用于查精确财务数字（用 sql_query）。"""
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "company": {"type": "string", "description": "公司名（可选）"},
            "top_k": {"type": "integer", "description": "返回条数，默认5"}
        },
        "required": ["query"]
    }
    priority = 80

    def __init__(self):
        self._rag = None

    def _get_rag(self):
        if self._rag is None:
            from rag_module import RAGRetriever
            self._rag = RAGRetriever()
            self._rag.load()
        return self._rag

    async def execute(self, query: str = "", company: str = "", top_k: int = 5) -> str:
        """异步执行 — 启用 FAISS+BM25+RRF 混合检索"""
        import json as _json
        from domain.retriever.hybrid import get_hybrid_retriever
        retriever = get_hybrid_retriever()
        rewritten = retriever.rewrite_query(query, company)
        result = retriever.search(rewritten, company=company, top_k=top_k)
        return _json.dumps(result, ensure_ascii=False, default=str)

    def run(self, inputs: dict) -> dict:
        query = inputs.get("query", "")
        company = inputs.get("company", "")
        top_k = inputs.get("top_k", 3)

        if not query:
            return {"success": False, "error": "缺少query参数"}

        # 拼接搜索词
        search_query = query
        if company and company not in query:
            search_query = company + " " + query

        try:
            rag = self._get_rag()
            results = []

            # 搜chunks
            if rag.chunks_index:
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

            return {
                "success": bool(results),
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
