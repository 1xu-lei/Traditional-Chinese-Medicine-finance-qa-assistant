# -*- coding: utf-8 -*-
"""
RAG Pipeline 配置
路径、数据库、Ollama 等全局常量
"""
import os

def _cfg_password():
    """凭据来源：环境变量 > 项目内 config/config.yaml（该文件已 gitignore，不入库）"""
    import os as _os
    v = _os.environ.get("DB_PASSWORD")
    if v:
        return v
    try:
        import yaml
        d = _os.path.dirname(_os.path.abspath(__file__))
        for _ in range(4):
            p = _os.path.join(d, "config", "config.yaml")
            if _os.path.exists(p):
                cfg = yaml.safe_load(open(p, encoding="utf-8")) or {}
                return (cfg.get("database") or {}).get("password", "")
            d = _os.path.dirname(d)
    except Exception:
        pass
    return ""


# ===== 路径配置 =====
BASE_DIR = r'D:\python-leanrn\codex'
MD_DIR = os.path.join(BASE_DIR, 'data', '研报总MD')
RAG_INDEX_DIR = os.path.join(BASE_DIR, 'models', 'rag_index')

# ===== FAISS/BM25 索引文件 =====
FAISS_INDEX_PATH = os.path.join(RAG_INDEX_DIR, 'faiss.index')
BM25_PATH = os.path.join(RAG_INDEX_DIR, 'bm25.pkl')
INDEX_META_PATH = os.path.join(RAG_INDEX_DIR, 'index_meta.json')

# ===== 中间产物 =====
CHUNKS_JSON_PATH = os.path.join(BASE_DIR, 'data', 'chunks.json')

# ===== MySQL 配置 =====
DB_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'database': 'finance_data',
    'user': 'root',
    'password': _cfg_password(),
}
TABLE_NAME = 'rag_chunks'

# ===== Ollama 配置 =====
OLLAMA_BASE_URL = 'http://localhost:11434'
EMBED_MODEL = 'bge-m3'
EMBED_DIM = 1024
RERANK_MODEL = 'qwen3.5:9b'

# ===== 分块参数 =====
MIN_TOKENS = 100
MAX_TOKENS = 512
MIN_CHUNK_TOKENS = 50

# ===== 检索参数 =====
VECTOR_TOP_K = 20
BM25_TOP_K = 20
RRF_FUSE_TOP_K = 20
RERANK_TOP_K = 3

# ===== 页脚截断关键词 =====
FOOTER_KEYWORDS_BASE = [
    '免责声明', '评级说明', '分析师声明',
    '重要声明', '风险提示声明',
    '不构成投资建议', '版权属于', '未经书面授权',
]

# ===== 公司名字典 =====
COMPANY_NAMES = [
    '华润三九', '华润双鹤', '云南白药',
    '以岭药业', '众生药业', '佐力药业',
    '信邦制药', '健民集团', '千金药业',
    '华森制药', '华神科技', '启迪药业',
    '嘉应制药', '太极集团', '太龙药业',
    '中恒集团', '昆药集团', '马应龙',
    '江中药业', '西藏药业', '方盛制药',
    '步长制药', '片仔癀', '东阿阿胶',
    '仁和药业', '亚宝药业', '奇正藏药',
    '康恩贝', '康美药业', '康芝药业',
    '振东制药', '新天药业', '桂林三金',
    '广誉远', '羚锐制药', '同仁堂',
    '白云山', '天士力', '金花股份',
    '达仁堂', '香雪制药', '陇神戎发',
    '科伦药业', '贵州百灵', '万邦德', '九芝堂',
]
COMPANY_ALIAS = {
    # === 股票代码/简称 ===
    '999': '华润三九', '双鹤': '华润双鹤',
    '538': '云南白药', '436': '片仔癀',
    '085': '同仁堂',
    # === 隐晦别名（正文深度匹配用）===
    '民族瑰宝': '片仔癀',
    '精品国药': '片仔癀',
    '片仔癀药业': '片仔癀',
    '百年传承': '云南白药',
    '云药': '云南白药',
}
REPORT_TYPE_PATTERNS = [
    (r'年报.*点评|年报.*业绩', '年报点评'),
    (r'半年报.*点评|半年报.*业绩|H1|半年', '半年报点评'),
    (r'三季报.*点评|三季报.*业绩|Q3', '三季报点评'),
    (r'一季报.*点评|一季报.*业绩|Q1', '一季报点评'),
    (r'首次覆盖', '首次覆盖'),
    (r'行业.*研究|行业.*概览|行业.*白皮书|行业.*策略', '行业研究'),
    (r'业绩预告|业绩快报', '业绩预告'),
    (r'公司.*点评|点评报告', '公司点评'),
]
