# -*- coding: utf-8 -*-
"""
RAG 检索模块 — 统一封装研报向量库 + 财务知识库的查询接口
"""
import pickle, os, sys, types
import pymysql
import numpy as np
import faiss
import requests
import json

class OllamaEmbedding:
    """Ollama BGE-M3 嵌入，替代 SentenceTransformer 避免 HF 下载"""
    def __init__(self, model_name="bge-m3", base_url="http://localhost:11434"):
        self.model_name = model_name
        self.base_url = base_url
        self._dim = 1024  # bge-m3 固定 1024 维

    def encode(self, texts, convert_to_tensor=False, normalize_embeddings=True):
        if isinstance(texts, str):
            texts = [texts]
        resp = requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model_name, "input": texts},
            timeout=300
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data["embeddings"]
        import numpy as np
        arr = np.array(embeddings, dtype=np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            arr = arr / (norms + 1e-10)
        if convert_to_tensor:
            import torch
            return torch.from_numpy(arr)
        return arr

    def get_sentence_embedding_dimension(self):
        return self._dim
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

# ==================== 路径配置 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_INDEX_DIR = os.path.join(BASE_DIR, "data", "report_index")
FINANCIAL_KB_DIR = os.path.join(BASE_DIR, "data", "financial_kb")

EMBEDDING_MODEL = "bge-m3"


# ==================== LlamaIndex pkl hack ====================
class _HackUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if "llama_index" in module:
            if "NodeRelationship" in name or "ObjectType" in name:
                return int
            return type("FakeNode", (), {})
        return super().find_class(module, name)


def _load_report_nodes():
    """加载研报索引的 nodes.pkl（绕过 LlamaIndex 依赖）"""
    path = os.path.join(REPORT_INDEX_DIR, "nodes.pkl")
    with open(path, "rb") as f:
        return _HackUnpickler(f).load()



# ==================== BM25Okapi 纯 Python 实现 ====================

def _tokenize_chinese(text):
    """中文分词：结巴分词，中文词保留，英文数字按原样"""
    import jieba
    words = jieba.lcut(text)
    tokens = []
    for w in words:
        w = w.strip()
        if not w:
            continue
        if any('\u4e00' <= c <= '\u9fff' for c in w):
            tokens.append(w)
        else:
            tokens.append(w.lower())
    return tokens


class BM25Okapi:
    """BM25-Okapi 实现"""
    def __init__(self, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = 0
        self.avg_doc_len = 0
        self.doc_freqs = []
        self.idf = {}
        self.doc_len = []
        self.corpus = []

    def fit(self, corpus):
        """corpus: list of str"""
        self.corpus = corpus
        nd = []
        num_doc = 0
        for doc in corpus:
            tokens = _tokenize_chinese(doc)
            self.doc_len.append(len(tokens))
            num_doc += len(tokens)
            freq = Counter(tokens)
            self.doc_freqs.append(freq)
            nd.append(set(tokens))
        self.corpus_size = len(corpus)
        self.avg_doc_len = num_doc / self.corpus_size if self.corpus_size else 1

        df = Counter()
        for doc_tokens in nd:
            for t in doc_tokens:
                df[t] += 1
        for term, freq in df.items():
            self.idf[term] = math.log((self.corpus_size - freq + 0.5) / (freq + 0.5) + 1)

    def get_scores(self, query):
        """返回每个文档的 BM25 分数"""
        query_tokens = _tokenize_chinese(query)
        scores = []
        for i in range(self.corpus_size):
            score = 0.0
            qf = Counter(query_tokens)
            for q, qcnt in qf.items():
                if q not in self.idf:
                    continue
                idf = self.idf[q]
                tf = self.doc_freqs[i].get(q, 0)
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avg_doc_len)
                score += idf * (tf * (self.k1 + 1)) / denom if denom > 0 else 0
            scores.append(score)
        return scores

    def search(self, query, top_k=5):
        scores = self.get_scores(query)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [(idx, scores[idx]) for idx in top_indices]



class RAGRetriever:
    """RAG 检索器 — 管理两个向量库的统一查询"""

    def __init__(self, model_name=EMBEDDING_MODEL):
        self.model_name = model_name
        self.embedder = None
        self.report_index = None
        self.report_nodes = None
        self.financial_index = None
        self.financial_data = None
        self._loaded = False
        self.bm25_reports = None
        self.bm25_financial = None

        self.mysql_conn = None
        self.cursor = None
        self._mysql_ready = False
    def load(self):
        """加载所有索引和模型（首次调用时自动下载 BGE-M3）"""
        if self._loaded:
            return

        print("[RAG] Loading embedding model via Ollama:", self.model_name)
        self.embedder = OllamaEmbedding(model_name=self.model_name)
        dim = self.embedder.get_sentence_embedding_dimension()
        print(f"[RAG] Embedding dim: {dim}")

        # --- 研报向量库 ---
        report_faiss = os.path.join(REPORT_INDEX_DIR, "faiss_index.bin")
        if os.path.exists(report_faiss):
            self.report_index = faiss.read_index(report_faiss)
            self.report_nodes = _load_report_nodes()
            print(f"[RAG] Report index: {self.report_index.ntotal} vectors")
        else:
            print("[RAG] Report index not found, skipped")

        # --- 财务知识库 ---
        fin_faiss = os.path.join(FINANCIAL_KB_DIR, "financial_faiss.bin")
        if os.path.exists(fin_faiss):
            self.financial_index = faiss.read_index(fin_faiss)
            with open(os.path.join(FINANCIAL_KB_DIR, "financial_metadata.pkl"), "rb") as f:
                self.financial_data = pickle.load(f)
            print(f"[RAG] Financial KB: {self.financial_index.ntotal} vectors")
        else:
            print("[RAG] Financial KB not found, skipped")

        # --- 新版研报 Chunks 索引 ---
        self.load_chunks_index()

        self._loaded = True
        print("[RAG] Building BM25 indexes...")
        self._build_bm25_indexes()
        print("[RAG] Ready!")

        # --- MySQL ---
        self._init_mysql()

    def search_reports(self, query, top_k=3):
        """搜研报：返回 top_k 条最相关的研报片段"""
        if not self.report_index:
            return []
        q_vec = self.embedder.encode(query, normalize_embeddings=True)
        D, I = self.report_index.search(q_vec.reshape(1, -1), top_k)

        results = []
        for idx, score in zip(I[0], D[0]):
            if idx < 0 or idx >= len(self.report_nodes):
                continue
            node = self.report_nodes[idx]
            inner = node.__dict__.get("__dict__", {})
            meta = inner.get("metadata", {})
            text = inner.get("text", "")
            results.append({
                "text": text,
                "file_name": meta.get("file_name", ""),
                "date": meta.get("creation_date", ""),
                "score": float(score),
            })
        return results

    def search_financial_kb(self, query, top_k=5):
        """搜财务知识库：返回 top_k 条最相关的财务数据摘要"""
        if not self.financial_index or not self.financial_data:
            return []
        q_vec = self.embedder.encode(query, normalize_embeddings=True)
        D, I = self.financial_index.search(q_vec.reshape(1, -1), top_k)

        chunks = self.financial_data.get("chunks", [])
        metadata = self.financial_data.get("metadata", [])

        results = []
        for idx, score in zip(I[0], D[0]):
            if idx < 0 or idx >= len(chunks):
                continue
            results.append({
                "text": chunks[idx],
                "meta": metadata[idx] if idx < len(metadata) else {},
                "score": float(score),
            })
        return results

    def search_all(self, query, top_k=3):
        """综合搜索：同时搜研报和财务知识库"""
        return {
            "reports": self.search_reports(query, top_k),
            "financial_kb": self.search_financial_kb(query, top_k),
        }


    # ==================== BM25 索引构建 ====================
    def _build_bm25_indexes(self):
        """构建 BM25 索引（研报 + 财务知识库）"""
        # 研报 BM25
        if self.report_nodes:
            report_texts = []
            for node in self.report_nodes:
                inner = node.__dict__.get('__dict__', {})
                text = inner.get('text', '')
                meta = inner.get('metadata', {})
                full = (meta.get('file_name', '') + ' ' + text)[:2000]
                report_texts.append(full)
            if report_texts:
                self.bm25_reports = BM25Okapi()
                self.bm25_reports.fit(report_texts)
                print(f'  [RAG] BM25 report index: {len(report_texts)} docs')

        # 财务知识库 BM25
        if self.financial_data:
            chunks = self.financial_data.get('chunks', [])
            if chunks:
                self.bm25_financial = BM25Okapi()
                self.bm25_financial.fit(chunks)
                print(f'  [RAG] BM25 financial KB: {len(chunks)} docs')

    # ==================== FAISS + BM25 混合检索 (RRF 融合) ====================
    def _rrf_fuse(self, bm25_results, faiss_results, k=60, top_k=5):
        """RRF 融合 BM25 和 FAISS 的排序结果"""
        scores = {}
        for rank, (idx, sc) in enumerate(bm25_results):
            scores[idx] = scores.get(idx, 0) + 1.0 / (k + rank)
        for rank, (idx, sc) in enumerate(faiss_results):
            scores[idx] = scores.get(idx, 0) + 1.0 / (k + rank)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return ranked

    def search_hybrid_reports(self, query, top_k=3):
        """混合检索研报：BM25 + FAISS + RRF"""
        if not self.report_index or self.bm25_reports is None:
            return self.search_reports(query, top_k)

        # BM25
        bm25_raw = self.bm25_reports.search(query, top_k=top_k * 3)

        # FAISS
        q_vec = self.embedder.encode(query, normalize_embeddings=True)
        D, I = self.report_index.search(q_vec.reshape(1, -1), top_k * 3)
        faiss_raw = [(idx, float(D[0][pos])) for pos, idx in enumerate(I[0]) if idx >= 0]

        # RRF
        fused = self._rrf_fuse(bm25_raw, faiss_raw, top_k=top_k)

        results = []
        for idx, rrf_score in fused:
            if idx < 0 or idx >= len(self.report_nodes):
                continue
            node = self.report_nodes[idx]
            inner = node.__dict__.get('__dict__', {})
            meta = inner.get('metadata', {})
            text = inner.get('text', '')
            results.append({
                'text': text,
                'file_name': meta.get('file_name', ''),
                'date': meta.get('creation_date', ''),
                'score': round(rrf_score, 4),
                'rrf_score': round(rrf_score, 4),
            })
        return results

    def search_hybrid_financial(self, query, top_k=5):
        """混合检索财务知识库：BM25 + FAISS + RRF"""
        if not self.financial_index or self.bm25_financial is None:
            return self.search_financial_kb(query, top_k)

        # BM25
        bm25_raw = self.bm25_financial.search(query, top_k=top_k * 3)

        # FAISS
        q_vec = self.embedder.encode(query, normalize_embeddings=True)
        D, I = self.financial_index.search(q_vec.reshape(1, -1), top_k * 3)
        faiss_raw = [(idx, float(D[0][pos])) for pos, idx in enumerate(I[0]) if idx >= 0]

        # RRF
        fused = self._rrf_fuse(bm25_raw, faiss_raw, top_k=top_k)

        chunks = self.financial_data.get('chunks', [])
        metadata = self.financial_data.get('metadata', [])

        results = []
        for idx, rrf_score in fused:
            if idx < 0 or idx >= len(chunks):
                continue
            results.append({
                'text': chunks[idx],
                'meta': metadata[idx] if idx < len(metadata) else {},
                'score': round(rrf_score, 4),
            })
        return results

    def search_all_hybrid(self, query, top_k=3):
        """综合混合搜索"""
        return {
            'reports': self.search_hybrid_reports(query, top_k),
            'financial_kb': self.search_hybrid_financial(query, top_k),
        }



    # ==================== Chunks Index (Phase 2) ====================
    def load_chunks_index(self):
        """加载新版 chunks.json 的 FAISS + BM25 + 元数据索引"""
        fdir = os.path.join(BASE_DIR, "models", "rag_index")
        fpath = os.path.join(fdir, "faiss.index")
        bpath = os.path.join(fdir, "bm25.pkl")
        mpath = os.path.join(fdir, "index_meta.json")
        if not os.path.exists(fpath):
            print("[RAG] Chunks index not found, skipped")
            return False
        self.chunks_index = faiss.read_index(fpath)
        with open(bpath, "rb") as f:
            self.chunks_bm25 = pickle.load(f)
        with open(mpath, encoding="utf-8") as f:
            self.chunks_meta = json.load(f)
        print(f"[RAG] Chunks index: {self.chunks_index.ntotal} vectors, {len(self.chunks_meta)} entries")
        return True

    def search_chunks_hybrid(self, query, top_k=5):
        """混合检索研报 chunks: BM25 + FAISS + RRF，返回带元数据的结果"""
        if not hasattr(self, "chunks_index") or self.chunks_index is None:
            return []
        # BM25
        bm25_raw = self.chunks_bm25.search(query, top_k=top_k * 3)
        # FAISS
        q_vec = self.embedder.encode(query, normalize_embeddings=True)
        D, I = self.chunks_index.search(q_vec.reshape(1, -1), top_k * 3)
        faiss_raw = [(idx, float(D[0][pos])) for pos, idx in enumerate(I[0]) if idx >= 0]
        # RRF fuse
        fused = self._rrf_fuse(bm25_raw, faiss_raw, top_k=top_k)
        results = []
        for idx, rrf_score in fused:
            if idx < 0 or idx >= len(self.chunks_meta):
                continue
            meta = self.chunks_meta[idx]
            results.append({
                "text": meta.get("text", ""),
                "meta": meta,
                "score": round(rrf_score, 4),
            })
        return results

    def search_all_with_chunks(self, query, top_k=3):
        """综合搜索：研报 + 财务知识库 + 研报 chunks"""
        result = self.search_all_hybrid(query, top_k)
        result["chunks"] = self.search_chunks_hybrid(query, top_k)
        return result

    # ==================== MySQL 元数据预过滤 (Phase 2) ====================
    def _init_mysql(self):
        """初始化 MySQL 连接用于元数据预过滤"""
        try:
            from rag_pipeline.config import DB_CONFIG
            self.mysql_conn = pymysql.connect(**DB_CONFIG)
            self.cursor = self.mysql_conn.cursor()
            self._mysql_ready = True
        except Exception as e:
            print(f"[RAG] MySQL init failed: {e}")
            self._mysql_ready = False

    def retrieve(self, query, company=None, year=None, top_k=20):
        """带元数据预过滤的检索
        Args:
            query: 查询文本
            company: 公司名（可选）
            year: 年份（可选）
            top_k: 返回数量
        """
        valid_ids = self._get_valid_ids(company, year)
        # MySQL预过滤失败时，用内存过滤
        if valid_ids is None and (company or year):
            valid_ids = self._get_valid_ids_memory(company, year)
            if valid_ids is None:
                # 指定了过滤条件但无匹配 → 空结果
                return []
        faiss_results = self._faiss_search(query, valid_ids, top_k)
        bm25_results = self._bm25_search(query, valid_ids, top_k)
        return self._rrf_merge(faiss_results, bm25_results)

    def _get_valid_ids_memory(self, company=None, year=None):
        """从index_meta.json内存过滤符合条件的chunk id"""
        if not hasattr(self, "chunks_meta") or not self.chunks_meta:
            return None
        ids = []
        for i, m in enumerate(self.chunks_meta):
            if company and m.get("company", "") != company:
                continue
            if year and str(m.get("year", "")) != str(year):
                continue
            ids.append(i)
        if not ids:
            print(f"[RAG] memory filter: no chunks for company={company} year={year}")
        return ids if ids else None

    def _get_valid_ids(self, company=None, year=None):
        """从MySQL获取符合条件的chunk id列表"""
        if not company and not year:
            return None
        if not self._mysql_ready:
            print("[RAG] MySQL not ready, skip pre-filter")
            return None
        conds, params = [], []
        if company:
            conds.append("company = %s"); params.append(company)
        if year:
            conds.append("year = %s"); params.append(year)
        sql = "SELECT id FROM rag_chunks WHERE " + " AND ".join(conds) + " ORDER BY id"
        try:
            self.cursor.execute(sql, params)
            ids = [r[0] for r in self.cursor.fetchall()]
            if not ids:
                print(f"[RAG] no chunks: company={company} year={year}")
            return ids
        except Exception as e:
            print(f"[RAG] MySQL query failed: {e}, skip pre-filter")
            self._mysql_ready = False
            return None

    def _faiss_search(self, query, valid_ids, top_k):
        """FAISS 检索，支持 IDSelector 过滤"""
        q_vec = self.embedder.encode([query], normalize_embeddings=True).astype(np.float32)
        if valid_ids is not None and len(valid_ids) > 0:
            sel = faiss.IDSelectorArray(np.array(valid_ids, dtype=np.int64))
            p = faiss.SearchParameters(); p.sel = sel
            scores, indices = self.chunks_index.search(q_vec, top_k, params=p)
        else:
            scores, indices = self.chunks_index.search(q_vec, top_k)
        return [{"id": int(idx), "score": float(s)} for idx, s in zip(indices[0], scores[0]) if idx >= 0]

    def _bm25_search(self, query, valid_ids, top_k):
        """BM25 检索，可选按 valid_ids 过滤"""
        bm25_raw = self.chunks_bm25.search(query, top_k=top_k * 5)
        valid_set = set(valid_ids) if valid_ids else None
        return [{"id": idx, "score": s} for idx, s in bm25_raw if valid_set is None or idx in valid_set][:top_k]

    def _rrf_merge(self, faiss_results, bm25_results, k=60):
        """RRF 融合排序"""
        scores = {}
        for r, item in enumerate(faiss_results):
            scores[item["id"]] = scores.get(item["id"], 0) + 1.0 / (k + r + 1)
        for r, item in enumerate(bm25_results):
            scores[item["id"]] = scores.get(item["id"], 0) + 1.0 / (k + r + 1)
        results = []
        for doc_id, rrf_score in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:20]:
            if doc_id < 0 or doc_id >= len(self.chunks_meta):
                continue
            m = self.chunks_meta[doc_id]
            results.append({"text": m.get("text",""), "meta": m, "score": round(rrf_score, 4)})
        return results


    # ==================== Reranker ====================
    def _rerank_bge(self, query, candidates, top_k=3):
        texts = [c.get("text","")[:200] for c in candidates]
        if not texts:
            return candidates[:top_k]
        pairs = ["为这个句子生成表示以用于检索相关文章：query: %s passage: %s" % (query, t) for t in texts]
        try:
            import numpy as np
            import requests
            r = requests.post("http://localhost:11434/api/embed", json={"model": "bge-m3", "input": pairs}, timeout=60)
            embs = np.array(r.json()["embeddings"], dtype=np.float32)
            r2 = requests.post("http://localhost:11434/api/embed", json={"model": "bge-m3", "input": ["为这个句子生成表示以用于检索相关文章：query: %s" % query]}, timeout=60)
            q_emb = np.array(r2.json()["embeddings"][0], dtype=np.float32)
            scores = embs @ q_emb / (np.linalg.norm(embs, axis=1) * np.linalg.norm(q_emb) + 1e-10)
            order = np.argsort(-scores)
            reranked = [dict(candidates[int(i)]) for i in order[:top_k]]
            for j, i in enumerate(order[:top_k]):
                reranked[j]["score"] = round(float(scores[i]), 4)
            return reranked
        except Exception as e:
            print("Rerank failed:", e)
            return candidates[:top_k]

    def search_with_rerank(self, query, company=None, year=None, retrieve_top_k=20, rerank_top_k=3):
        candidates = self.retrieve(query, company=company, year=year, top_k=retrieve_top_k)
        if not candidates:
            return []
        return self._rerank_bge(query, candidates, top_k=rerank_top_k)


# ==================== 测试入口 ====================
if __name__ == "__main__":
    rag = RAGRetriever()
    rag.load()

    questions = [
        "金花股份2023年利润总额",
        "医药行业研发投入趋势",
        "哪些中药企业盈利能力较强",
    ]

    for q in questions:
        print("\n" + "=" * 60)
        print("Query:", q)
        results = rag.search_chunks_hybrid(q, top_k=3)

        for i, r in enumerate(results):
            meta = r.get("meta", {})
            company = meta.get("company", "?")
            section = meta.get("section", "?")
            year = meta.get("year", "?")
            print(f"  [{i}] score={r['score']:.4f} | {company} | {year} | {section}")
            print(f"    {r['text'][:150]}...")

    print("\nTest done!")
