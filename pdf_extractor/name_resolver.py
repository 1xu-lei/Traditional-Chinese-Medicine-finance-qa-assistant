import os
# -*- coding: utf-8 -*-
"""name_resolver.py: 公司中文名 -> 股票代码 映射
从DB加载，支持精确/相似度匹配"""
import pymysql, re
from difflib import SequenceMatcher as SM

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


DB = {"host":"localhost","port":3306,"user":"root","password":_cfg_password(),"database":"finance_data","charset":"utf8mb4"}

def load_map():
    """从income_sheet加载 {中文名: 股票代码} 映射"""
    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT stock_code, stock_abbr FROM income_sheet")
    rows = cur.fetchall()
    cur.close(); conn.close()
    # 构建精确映射
    exact = {}
    fuzzy = []
    for sc, name in rows:
        if not name: continue
        clean = name.strip().replace(" ", "").replace("\u3000", "")
        exact[clean] = sc
        fuzzy.append((clean, sc))
    return exact, fuzzy

# 全局缓存
_EXACT_MAP = None
_FUZZY_LIST = None


def normalize_name(name):
    """??????????????????????"""
    name = name.replace(" ", "").replace("\u3000", "")
    for s in ["??????", "????", "??", "??"]:
        name = name.replace(s, "")
    return name.strip()

def ensure_loaded():
    global _EXACT_MAP, _FUZZY_LIST
    if _EXACT_MAP is None:
        _EXACT_MAP, _FUZZY_LIST = load_map()

def resolve(name_raw):
    """中文名 -> 股票代码, 置信度(%)
先精确匹配, 再移除噪音后匹配, 最后相似度匹配"""
    ensure_loaded()
    name = normalize_name(name_raw)
    if not name: return None, 0

    # 1) 精确匹配
    if name in _EXACT_MAP:
        code = _EXACT_MAP[name]
        if len(code) == 4 and not code.startswith("6"):
            code = "00" + code
        return code, 100

    # 2) 移除常见后缀再匹配
    for suffix in ["股份有限公司", "股份公司", "有限公司", "集团股份", "集团", "制药", "药业", "医药"]:
        stripped = name
        if suffix in stripped:
            stripped = stripped.replace(suffix, "")
            if stripped in _EXACT_MAP:
                code = _EXACT_MAP[stripped]
                if len(code) == 4 and not code.startswith("6"):
                    code = "00" + code
                return code, 95

    # 3) 相似度匹配 (阈值70%)
    best, best_score = None, 0
    for std_name, sc in _FUZZY_LIST:
        # 如果name完全包含在std_name中或反之
        if name in std_name or std_name in name:
            code2 = sc
            if len(code2) == 4 and not code2.startswith("6"):
                code2 = "00" + code2
            return code2, 90
        sim = SM(None, name, std_name).ratio() * 100
        if sim > best_score:
            best, best_score = sc, sim

    if best and best_score >= 70:
        # ??????0?6?: 2082 -> 002082
        if len(best) == 4 and not best.startswith("6"):
            best = "00" + best
        return best, int(best_score)
    return None, 0

def batch_resolve(names):
    """批量解析, 返回 {name: (code, confidence)}"""
    return {n: resolve(n) for n in names}

if __name__ == "__main__":
    ensure_loaded()
    print("已加载 %d 个公司名映射" % len(_EXACT_MAP))
    # 测试几个深交所可能遇到的名称
    tests = ["云南白药", "九 芝 堂", "东阿阿胶", "华润三九", "以岭药业", "桂林三金", "片仔癀"]
    for t in tests:
        code, conf = resolve(t)
        print("  %s -> %s (conf=%d)" % (t, code, conf))
