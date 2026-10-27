# -*- coding: utf-8 -*-
"""company_code_map.py: 静态公司名→股票代码映射
解决name_resolver依赖数据库的鸡生蛋问题"""
import os, re, json

# 静态映射表（常用中药/医药公司）
_COMPANY_MAP = {
    "云南白药": "000538", "华润三九": "000999", "片仔癀": "600436",
    "同仁堂": "600085", "白云山": "600332", "东阿阿胶": "000423",
    "九芝堂": "000989", "马应龙": "600993", "太极集团": "600129",
    "以岭药业": "002603", "桂林三金": "002275", "步长制药": "603858",
    "天士力": "600535", "康缘药业": "600557", "昆药集团": "600422",
    "江中药业": "600750", "千金药业": "600479", "仁和药业": "002589",
    "达仁堂": "600329", "广誉远": "600771", "健民集团": "600976",
    "羚锐制药": "600285", "亚宝药业": "600351", "方盛制药": "603998",
    "奇正藏药": "002287", "众生药业": "002317", "益盛药业": "002566",
    "佐力药业": "300181", "贵州百灵": "002424", "精华制药": "002349",
    "莱茵生物": "002166", "嘉应制药": "002198", "特一药业": "002728",
    "康弘药业": "002773", "信邦制药": "002390", "万邦德": "002082",
    "通化金马": "002766", "瑞康医药": "002589", "康恩贝": "600572",
    "红日药业": "300026", "新光药业": "300519", "盘龙药业": "002864",
    "陇神戎发": "300534", "太龙药业": "600222", "中恒集团": "600252",
    "金花股份": "600080", "康惠制药": "603139", "天目药业": "600671",
    "长药控股": "300391", "上海医药": "601607", "白云山": "600332",
    "新华制药": "000756", "鲁抗医药": "600789", "海正药业": "600267",
    "誉衡药业": "002437", "沃华医药": "002107", "佛慈制药": "002644",
    "汉森制药": "002412", "振东制药": "300158", "上海凯宝": "300039",
    "康泰医学": "300869", "粤万年青": "301111", "华森制药": "002907",
    "新里程": "002219", "吉林敖东": "000623", "金陵药业": "000919",
    "海南海药": "000566", "紫鑫药业": "002118", "益佰制药": "600594",
    "易明医药": "002826", "大理药业": "603963", "龙津药业": "002750",
    "沃华医药": "002107",
}

# 从文件名解析的公司名（去掉"：2025年..."部分）直接映射
_NAME_CACHE = None

def _load_from_filesystem():
    """从已有的JSON文件加载映射（作为补充）"""
    global _NAME_CACHE
    if _NAME_CACHE is not None:
        return
    _NAME_CACHE = dict(_COMPANY_MAP)

    # 也从extracted JSON加载
    for d in ['D:/python-leanrn/codex/data/extracted/sse', 'D:/python-leanrn/codex/data/extracted/szse']:
        if not os.path.exists(d):
            continue
        import glob
        for f in glob.glob(os.path.join(d, '*.json')):
            try:
                data = json.load(open(f, encoding='utf-8'))
                code = data.get('stock_code', '')
                name = data.get('stock_abbr', '')
                if code and name:
                    _NAME_CACHE[name.strip()] = code
            except:
                pass

def _pad_code(code):
    """补齐股票代码到6位"""
    if len(code) < 6:
        code = code.zfill(6)
    return code

def resolve(name_raw):
    """公司名→股票代码"""
    _load_from_filesystem()

    name = name_raw.strip().replace(" ", "").replace("　", "")

    # 精确匹配
    if name in _NAME_CACHE:
        code = _NAME_CACHE[name]
        code = _pad_code(code)
        return code, 100

    # 去后缀匹配
    for suffix in ["集团股份有限公司", "股份有限公司", "有限公司", "集团", "股份"]:
        stripped = name.replace(suffix, "").strip()
        if stripped in _NAME_CACHE:
            code = _pad_code(_NAME_CACHE[stripped])
            return code, 95

    # 包含匹配
    for std_name, code in _NAME_CACHE.items():
        if name in std_name or std_name in name:
            code = _pad_code(code)
            return code, 80

    return None, 0
