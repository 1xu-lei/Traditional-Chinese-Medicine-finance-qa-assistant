# -*- coding: utf-8 -*-
"""
build_index.py - Build FAISS + BM25 index from chunks.json
Usage: python build_index.py
Output: data/rag_index/faiss.index + bm25.pkl + index_meta.json
"""

import os, sys, json, pickle
import numpy as np
import faiss
from tqdm import tqdm

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from rag_pipeline.config import CHUNKS_JSON_PATH, RAG_INDEX_DIR
from rag_module import OllamaEmbedding, BM25Okapi
FAISS_PATH = os.path.join(RAG_INDEX_DIR, "faiss.index")
BM25_PATH = os.path.join(RAG_INDEX_DIR, "bm25.pkl")
META_PATH = os.path.join(RAG_INDEX_DIR, "index_meta.json")
EMBED_MODEL = "bge-m3"
BATCH_SIZE = 16


def main():
    os.makedirs(RAG_INDEX_DIR, exist_ok=True)
    print("[1/4] Loading chunks...")
    with open(CHUNKS_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chunks = data["chunks"]
    print(f"  Total chunks: {len(chunks)}")

    print("[2/4] Preparing texts and metadata...")
    texts = [c["text"] for c in chunks]
    metadata = []
    for c in chunks:
        metadata.append({
            "text": c["text"],
            "chunk_index": c.get("chunk_index"),
            "company": c.get("company", ""),
            "stock_code": c.get("stock_code"),
            "year": c.get("year"),
            "period": c.get("period"),
            "report_type": c.get("report_type", ""),
            "section": c.get("section", ""),
            "source_file": c.get("source_file", ""),
            "token_count": c.get("token_count", 0),
        })
    print(f"  Metadata items: {len(metadata)}")

    print("[3/4] Computing embeddings (Ollama bge-m3)...")
    embedder = OllamaEmbedding(model_name=EMBED_MODEL)
    all_embeddings = []
    pbar = tqdm(total=len(texts), desc="Embedding", unit="texts", ncols=80)
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        emb = embedder.encode(batch, normalize_embeddings=True)
        all_embeddings.append(emb)
        pbar.update(len(batch))
    pbar.close()
    embeddings = np.vstack(all_embeddings).astype(np.float32)
    print(f"  Embedding shape: {embeddings.shape}")

    print("[4/4] Building FAISS and BM25...")
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    faiss.write_index(index, FAISS_PATH)
    print(f"  FAISS saved: {index.ntotal} vectors")

    bm25 = BM25Okapi()
    bm25.fit(texts)
    with open(BM25_PATH, "wb") as f:
        pickle.dump(bm25, f)
    print(f"  BM25 saved: {bm25.corpus_size} docs")

    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"  Metadata saved: {len(metadata)} entries")
    print("Done!")


if __name__ == "__main__":
    main()