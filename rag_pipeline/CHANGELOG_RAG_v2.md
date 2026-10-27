# ============================================================
# 二期 RAG Pipeline 改动总结
# 日期：2026-05-29
# ============================================================

# --- 1. 页脚截断改进 ---
# 文件：rag_pipeline/document_cleaner.py
# 改动：
#   旧方案：全文关键词匹配，碰到就截断
#   新方案：按行扫描 + 段落边界截断 + 10%长度守卫
#   新增模式：'本人具有', '中国证券业协会'
# 效果：页脚关键词残留从 20 条降到 0
# 函数：clean_footer()

# --- 2. 公司别名补充 ---
# 文件：rag_pipeline/config.py
# 改动：
#   新增隐晦别名映射：
#     民族瑰宝 → 片仔癀
#     精品国药 → 片仔癀
#     片仔癀药业 → 片仔癀
#     百年传承 → 云南白药
#     云药 → 云南白药
#   修复乱码：片仑瘦→片仔癀，九苙堂→九芝堂
# 效果："精品国药代表，民族瑰宝传承.md" 从"行业综述"正确识别为片仔癀

# --- 3. 双路索引构建 ---
# 文件：build_index.py（已有代码，首次跑通）
# 产出：data/rag_index/faiss.index + bm25.pkl + index_meta.json
# 规模：4306 vectors, 4306 BM25 docs, 4306 元数据条目
# 模型：bge-m3 1024维，通过 Ollama 调用

# --- 4. MySQL 入库 ---
# 新增脚本：手动执行入库
# 表：rag_chunks，id 与 FAISS 位置编号一一对应
# 验证：MIN(id)=0, MAX(id)=4305, COUNT=4306，完全对齐

# --- 5. 元数据预过滤检索 ---
# 文件：rag_module.py
# 新增方法：
#   retrieve(query, company, year, top_k)  — 主入口
#   _get_valid_ids(company, year)          — MySQL 查询
#   _faiss_search(query, valid_ids, top_k) — FAISS + IDSelectorArray
#   _bm25_search(query, valid_ids, top_k)  — BM25 + 集合过滤
#   _rrf_merge(faiss_results, bm25_results)— RRF 倒数排名融合
# 效果：搜"片仔癀 净利润"时先过滤 company=片仔癀，再检索，结果全为片仔癀

# --- 6. Reranker 精排 ---
# 文件：rag_module.py
# 新增方法：
#   _rerank_bge(query, candidates, top_k)  — bge-m3 query-aware 编码排序
#   search_with_rerank(query, company, year, retrieve_top_k, rerank_top_k) — 流水线入口
# 方案说明：因 CPU 无法跑 qwen3.5:9b（单条 30s+），改用 bge-m3 伪交叉编码
# 性能：20候选→3精排，单次 7秒
# 可迁移：后续 GPU 环境可直接替换为 cross-encoder，接口不变
