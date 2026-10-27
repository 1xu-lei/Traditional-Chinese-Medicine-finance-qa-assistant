# -*- coding: utf-8 -*-
"""llm_fallback.py: LLM兑底提取

⚠ DEPRECATED (2026-08-09): 此文件已被 ollama_repair.py v2.0 取代。
  旧版问题：整页法(15页/8000字符)、无 think:false、无单位换算、无恒等式复验。
  请使用: python pdf_extractor/ollama_repair.py --check / --json / --limit --apply
  本文件保留仅作参考，不再维护。
"""
import json, re, requests
URL = "http://localhost:11434/api/generate"
MODEL = "qwen3.5:9b"
PROMPT = '''你是财报数据提取专家。从以下文本中提取财务数据，返回JSON格式（只返回JSON）。
文本: {text}
{
  "income_sheet": {"total_operating_revenue": 万元, "net_profit": 万元, "operating_expense_cost_of_sales": 万元},
  "balance_sheet": {"asset_total_assets": 万元, "liability_total_liabilities": 万元, "equity_total_equity": 万元},
  "cash_flow_sheet": {"operating_cf_net_amount": 万元},
  "core_performance_indicators_sheet": {"eps": 元/股, "roe_weighted_excl_non_recurring": 小数}
}
找不到的设为null。'''
def extract_from_path(pdf_path):
    import pdfplumber as pp
    try:
        pdf = pp.open(pdf_path)
        pages = [pdf.pages[i].extract_text() or "" for i in range(min(15, len(pdf.pages)))]
        pdf.close()
        text = "\n".join(pages)[:8000]
    except: return None, "open error"
    try:
        r = requests.post(URL, json={"model":MODEL,"prompt":PROMPT.format(text=text),"stream":False,"options":{"temperature":0.1,"num_predict":2048}}, timeout=120)
        r.raise_for_status(); raw = r.json().get("response","")
    except Exception as e: return None, str(e)
    jm = re.search(r"\{.*\}", raw, re.DOTALL)
    if not jm: return None, "no json"
    try: data = json.loads(jm.group())
    except: return None, "json error"
    cleaned = {}
    for tbl in ["income_sheet","balance_sheet","cash_flow_sheet","core_performance_indicators_sheet"]:
        d = data.get(tbl,{})
        if not isinstance(d,dict): continue
        c = {}
        for k,v in d.items():
            if v is None: continue
            try: c[k] = round(float(str(v).replace(",","")),4)
            except: pass
        if c: cleaned[tbl] = c
    return cleaned, None
