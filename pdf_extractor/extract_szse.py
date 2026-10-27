# -*- coding: utf-8 -*-
"""extract_szse.py: 深交所PDF提取 -> JSON (四表独立搜索+去重)"""
import sys, os, json, time, re
sys.path.insert(0, "D:/python-leanrn/codex")
sys.path.insert(0, "D:/python-leanrn/codex/pdf_extractor/lib")
from pdf_extractor.file_parser import get_all_pdfs
from pdf_extractor.table_extractor import extract_all_tables
from pdf_extractor.column_recognizer import analyze
from pdf_extractor.field_mapper import match, set_learning_enabled
from pdf_extractor.value_normalizer import normalize
from pdf_extractor.confidence_scorer import P as score_p, F as score_f, V as score_v, C as score_c, calc as conf_calc, decide as conf_decide
from pdf_extractor.validator import validate_all, fmt_report, validation_score
from pdf_extractor.extraction_logger import ExtractionLogger

OUT = "D:/python-leanrn/codex/data/extracted/szse"
LOG_DIR = "D:/python-leanrn/codex/data/logs/extraction"
os.makedirs(OUT, exist_ok=True)

# ---- 测试模式 ----
import argparse
_parser = argparse.ArgumentParser(description='深交所 PDF 提取')
_parser.add_argument('--test', type=int, default=0, metavar='N',
                     help='只处理前 N 个 PDF (快速测试)')
# 返工22 P3-5: 定向重跑（无参数时行为与全量完全一致）
_parser.add_argument('--codes', default='', metavar='CODES',
                     help='只处理指定股票代码, 逗号分隔, 如 000538,002082')
_parser.add_argument('--list', dest='list_json', default='', metavar='JSON',
                     help='按清单JSON过滤(如 D:\\lunw\\_cpi_rerun_list.json 的 pdf 路径)')
_args, _unknown = _parser.parse_known_args()
_LIST_MODE = bool(_args.list_json)   # 返工22 P3-9: --list 定向重跑走 CPI 增量更新

all_pdfs = get_all_pdfs(r"D:\python-leanrn\codex\data\附件2：财务报告")
pdfs_raw = [p for p in all_pdfs if p["ex"] == "SZSE"]
pdfs_raw.sort(key=lambda x: x["sz"])


def _norm_path(pth):
    return os.path.normcase(os.path.abspath(str(pth or "").strip()))


# 返工22 P3-5: 清单过滤（清单里含上交所文件，本脚本只会命中深交所那部分）
if _args.list_json:
    with open(_args.list_json, encoding='utf-8') as _f:
        _spec = json.load(_f)
    _wanted = set()
    for _k, _v in _spec.items():
        _p = _v.get("pdf") if isinstance(_v, dict) else None
        if _p:
            _wanted.add(_norm_path(_p))
    _before = len(pdfs_raw)
    pdfs_raw = [p for p in pdfs_raw if _norm_path(p["fp"]) in _wanted]
    print("深交所清单过滤: %d → %d 个 (清单共 %d 项, 含上交所)" % (_before, len(pdfs_raw), len(_wanted)))
    _missing = _wanted - set(_norm_path(p["fp"]) for p in all_pdfs)
    if _missing:
        print("  [警告] 清单中 %d 个 PDF 路径在语料里找不到" % len(_missing))

# 返工22 P3-5: 代码过滤
if _args.codes:
    _codes = set(c.strip() for c in _args.codes.split(',') if c.strip())
    _before = len(pdfs_raw)
    pdfs_raw = [p for p in pdfs_raw
                if (p.get("code") in _codes) or any(p["fn"].startswith(c) for c in _codes)]
    print("深交所代码过滤 %s: %d → %d 个" % (sorted(_codes), _before, len(pdfs_raw)))


# ==== 返工22 P3-9: --list 定向重跑只做 CPI 增量更新，三表旧值一律保留 ====
_MAIN_SHEETS = ("income_sheet", "balance_sheet", "cash_flow_sheet")
_CPI = "core_performance_indicators_sheet"


def _apply_incremental(fp, merged_data):
    """定向重跑模式：目标 JSON 已存在时，三表保留旧值(含 ollama 补值 / TARGETED_FIXES /
    历史修复)，只覆盖 CPI；新 CPI 提取失败则保留旧 CPI 并打 cpi_rework_failed=True。"""
    if not _LIST_MODE or not os.path.exists(fp):
        return merged_data
    try:
        with open(fp, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return merged_data
    old_data = old.get("data", {}) if isinstance(old, dict) else {}

    new_cpi = merged_data.get(_CPI)
    out_data = dict(old_data)
    for s in _MAIN_SHEETS:
        if s in old_data:
            out_data[s] = old_data[s]
        elif s in merged_data:
            out_data[s] = merged_data[s]

    if new_cpi is not None:
        out_data[_CPI] = new_cpi
        if "cpi_rework_failed" in out_data:
            del out_data["cpi_rework_failed"]
    elif _CPI in old_data:
        out_data[_CPI] = old_data[_CPI]
        out_data["cpi_rework_failed"] = True
        print("  [P3-9] CPI 提取失败，保留旧 CPI 并打 cpi_rework_failed=true")

    return out_data

if _args.test > 0:
    pdfs = pdfs_raw[:_args.test]
    print("深交所共 %d 个PDF, 测试模式只处理 %d 个" % (len(pdfs_raw), len(pdfs)))
else:
    pdfs = pdfs_raw
    print("深交所共 %d 个PDF" % len(pdfs))
sys.stdout.flush()

TBL_CN = {"income_sheet":"利润表","balance_sheet":"资产负债表",
           "cash_flow_sheet":"现金流量表","core_performance_indicators_sheet":"核心业绩"}
TBL_ORDER = ["income_sheet","balance_sheet","cash_flow_sheet","core_performance_indicators_sheet"]
# M6: ????? - ???????
TBL_MIN_FIELDS = {
    "income_sheet": 4,
    "balance_sheet": 3,
    "cash_flow_sheet": 3,
    "core_performance_indicators_sheet": 2,
}


# 提取期间暂停学习，保证确定性
set_learning_enabled(False)

ok = fail = skip = 0
total_fields = 0
total_checks = 0
total_passed = 0
start = time.time()
for idx, p in enumerate(pdfs):
    sc, name, yr, rp = p["code"], p["name"], p["yr"], p["rp"]
    if not sc or not yr or not rp:
        skip += 1; fail += 1; continue

    fn_short = p["fn"][:40]

    # ---- 分类+策略 (2026-08-08): 懒分类，用到才扫描 ----
    pdf_type = p.get("type")
    if isinstance(pdf_type, str):
        # 旧版快速分类 → 升级为新版
        from pdf_extractor.pdf_classifier import classify_pdf as new_classify
        pdf_type = new_classify(p["fp"])
    from pdf_extractor.extraction_strategies import get_strategy, should_skip_table
    strategy = get_strategy(pdf_type, p.get("fn", ""))
    tbl_order = [tt for tt in TBL_ORDER if not should_skip_table(strategy, tt)]
    if len(tbl_order) < len(TBL_ORDER):
        skipped = set(TBL_ORDER) - set(tbl_order)
        print("  [分类] %s | 跳过: %s | 仅提取: %s" % (
            pdf_type.report_format,
            ",".join(TBL_CN.get(t,t) for t in skipped),
            ",".join(TBL_CN.get(t,t) for t in tbl_order)))

    # ---- 日志: 开始 PDF ----
    logger = ExtractionLogger(p["fp"], sc, yr, rp)

    try:
        all_tables = extract_all_tables(p["fp"])
    except Exception as e:
        import traceback
        print("  [ERROR] extract_all_tables 崩溃: %s" % e)
        traceback.print_exc()
        fail += 1
        continue
    source = "pdfplumber_standard"
    merged_data = {}
    low_conf_all = {}  # P0-3: 收集各表的低置信度映射

    if all_tables:
        for tt in tbl_order:
            res = all_tables.get(tt)
            if not res or not res.get("matrix"): continue

            sb = res["scores_breakdown"]
            total_score = res["score"]
            page_num = res["page"] + 1
            source_type = res.get("source_type", "statement")

            # 列识别
            ci = analyze(res["matrix"]["header_row"], yr, rp)
            tc = 1
            for c in ci:
                if c["type"] == "target": tc = c["index"]; break

            # 返工25: CPI 表"主要会计数据"有两组值列——
            #   流量/比率字段(营收/净利/eps/roe_w/经营现金流)取"年初至报告期末"累计列；
            #   时点字段(总资产/净资产等 balance_sheet 字段)取"本报告期末"列。
            # analyze() 把"本报告期"和"年初至报告期末"都判成 target，旧逻辑取第一个(=本报告期单季)，
            # 导致 roe_w/eps/营收 取到 Q3 单季值(济川2023Q3 roe_w=4.99 应 16.14、eps=0.66 应 2.123)。
            tc_cum = tc  # 累计列，默认退化为本报告期列(FY/H1 报告无"年初至报告期末"列时不受影响)
            if tt == "core_performance_indicators_sheet":
                hdr = res["matrix"].get("header_row", []) or []
                hdr_txt = "".join(re.sub(r"\s+", "", str(h or "")) for h in hdr)
                if "调整前" in hdr_txt or "调整后" in hdr_txt:
                    # 返工26: 追溯调整表——"年初至报告期末"列定位被"调整前/调整后"合并单元格带偏，
                    # 强行取会取到"调整前/变动幅度"列(太极2024Q3 从 0.0132 越修越错到 0.142)。
                    # 退回本报告期列，等返工26 专项处理(本单只保证不更错)。
                    tc_cum = tc
                else:
                    for idx, h in enumerate(hdr):
                        hs = re.sub(r"\s+", "", str(h or ""))  # 去掉换行/空格("年初至报告\n期末"→"年初至报告期末")
                        if "年初至报告期末" in hs and "增减" not in hs:
                            tc_cum = idx
                            break
                    else:
                        # 返工27: SZSE 表头缺"年初至报告期末"列(合并单元格丢 colspan → 表头被压缩)，
                        # 但数据行仍是 [label, 本报告期, 累计] 两值列(东阿阿胶/云南白药) → 累计列=最后一列。
                        # 仅 Q3 有独立累计列(Q1/H1 两者相同、FY 无累计列)。
                        if rp == "Q3":
                            n_cols = max((len(r) for r in res["matrix"].get("data_rows", []) if r), default=0)
                            if n_cols > tc + 1:
                                tc_cum = n_cols - 1

            # 字段映射 + 去重：同一field取最高conf
            best_per_field = {}  # {field_name: (value, conf, orig_label)}
            low_confidence = []  # P0-3: conf<60 的低置信度映射
            for row in res["matrix"]["data_rows"]:
                if len(row) <= tc: continue
                label = row[0]
                entry, conf = match(label, context_tbl=tt)
                if not entry: continue
                from pdf_extractor.field_mapper import learn
                learn(label, entry["f"], conf)
                # 返工25: 时点字段(balance_sheet)取本报告期末列，流量/比率字段取累计列
                use_tc = tc if (entry["t"] == "balance_sheet") else tc_cum
                if len(row) <= use_tc: continue
                # P0-3: conf<60 不进 best_per_field，但保留在 low_confidence 供排查
                if conf < 60:
                    raw_val = row[use_tc]
                    low_confidence.append({
                        "raw_label": label, "mapped_field": entry["f"],
                        "confidence": round(conf, 1), "raw_value": raw_val,
                    })
                    continue
                v = normalize(row[use_tc], res.get("unit_factor", 1), entry["f"], label=row[0])
                if v is None: continue
                fname = entry["f"]
                # 返工15修3: 同分平局时取行序靠前(标准年报合并表在母公司表前)
                if fname not in best_per_field:
                    best_per_field[fname] = (v, conf, label, row[use_tc])
                elif conf > best_per_field[fname][1]:
                    best_per_field[fname] = (v, conf, label, row[use_tc])
                # conf 相同时不替换，保留第一个匹配(行序靠前 = 合并表)

            # 保存低置信度映射
            if low_confidence:
                low_conf_all[tt] = low_confidence
                print("  [低置信度] %s: 跳过 %d 条 conf<60 映射" % (TBL_CN[tt], len(low_confidence)))

            if best_per_field:
                tbl_data = {f: v for f, (v, c, _, _) in best_per_field.items()}
                merged_data[tt] = tbl_data

                # ---- 日志: 表提取成功 ----
                logger.record_table(
                    table_type=tt, found=True,
                    score=total_score, page=page_num - 1,
                    extraction_method="section_based" if source_type == "statement" else "fallback",
                    unit_factor=res.get("unit_factor", 10000),
                    unit_raw=res.get("unit_raw", "万元"),
                    source_type=source_type,
                )
                logger.record_field_batch(tt, best_per_field,
                                          res.get("unit_factor", 10000),
                                          res.get("unit_raw", "万元"))

                # 打印摘要
                print("  [%d/%d] %s  yr=%d rp=%s | %s(p%d) %s 评分%d | %d字段" % (
                    idx+1, len(pdfs), fn_short, yr, rp, TBL_CN[tt], page_num,
                    source_type.upper(), total_score, len(tbl_data)))
                total_fields += len(tbl_data)
                for fname, val in sorted(tbl_data.items(), key=lambda x: x[0]):
                    _, conf, orig, _ = best_per_field[fname]
                    tag = "准确" if conf >= 80 else "模糊" if conf >= 60 else "存疑"
                    print("      %-40s = %12.4f  (←%s) conf=%.0f %s" % (fname, val, orig[:20], conf, tag))

    if not merged_data:
        print("  [%d/%d] %s  yr=%d rp=%s | FAIL" % (idx+1, len(pdfs), fn_short, yr, rp))
        logger.finish(success=False, total_fields=0)
        fail += 1; continue

    # 返工16修3: 移除多表交叉填充 — 每张表只保留本表提取的字段，表之间不互塞。
    # 旧逻辑把所有表字段并集复制进每张表，导致 BS 混入 net_profit/eps、四表键串味。

    # P1: 交叉填充后校验 — 防止脏值通过交叉填充污染多表
    from pdf_extractor.field_rules import validate_table_data
    cross_check = validate_table_data(merged_data)
    if cross_check["flagged_fields"] > 0:
        print("  [交叉填充校验] 移除 %d 个异常字段" % cross_check["flagged_fields"])
        for flag in cross_check["flags"]:
            tbl, fld = flag["table"], flag["field"]
            if tbl in merged_data and fld in merged_data[tbl]:
                del merged_data[tbl][fld]
        # 清理空表
        for tbl in list(merged_data.keys()):
            if not merged_data[tbl]:
                del merged_data[tbl]

    # 返工C: CPI摘要表单位交叉比对 — 与利润表/资产负债表同名字段比较
    cpi_tbl = "core_performance_indicators_sheet"
    if cpi_tbl in merged_data:
        cpi_data = merged_data[cpi_tbl]
        CPI_AMOUNT_FIELDS = [
            "total_operating_revenue", "net_profit",
            "asset_total_assets", "equity_total_equity",
            "operating_cf_net_amount",
        ]
        reference_tables = [t for t in ["income_sheet", "balance_sheet", "cash_flow_sheet"]
                           if t in merged_data]
        cpi_dropped = 0
        for fld in CPI_AMOUNT_FIELDS:
            if fld not in cpi_data:
                continue
            cpi_val = cpi_data[fld]
            if cpi_val is None:
                continue
            for ref_tbl in reference_tables:
                ref_val = merged_data[ref_tbl].get(fld)
                if ref_val is None or ref_val == 0:
                    continue
                ratio = abs(cpi_val / ref_val)
                # P0修复: 差约10000倍(单位元/万元互换)时，用主表值纠正CPI摘要表。
                # 摘要表金额样本少、单位检测易误判，主表金额样本多、单位可靠。
                # 阈值[2000,200000]覆盖"10000倍 × 季度/全年(≤20倍)"的复合差异，
                # 下界2000排除"母公司vs合并"(5~10倍)的口径差异(那是P0-2问题，不在此纠正)。
                if 2000 < ratio < 200000 or 2000 < 1/ratio < 200000:
                    print("  [CPI交叉] %s: CPI=%.2f vs %s=%.2f (差%.0fx) → 用主表值纠正" % (
                        fld, cpi_val, ref_tbl, ref_val, ratio))
                    cpi_data[fld] = ref_val
                    cpi_dropped += 1
                    break
        if cpi_dropped > 0:
            print("  [CPI交叉] 共丢弃 %d 个单位不一致的字段" % cpi_dropped)
        if not cpi_data:
            del merged_data[cpi_tbl]

    # M6 validation (before JSON to include in output)
    v_pass, v_total, v_results = validate_all(merged_data)
    total_checks += v_total
    total_passed += v_pass
    v_score = validation_score(merged_data)
    print("  [M6校验] %d/%d 通过 | %s" % (
        v_pass, v_total,
        ", ".join("%s=%s" % (r["check"], "OK" if r["passed"] else "FAIL") for r in v_results)))
    if v_results:
        for r in v_results:
            if not r["passed"]:
                print("    XX %s: %s" % (r["check"], r["detail"]))

    # 收集单位信息
    unit_info = {}
    if all_tables:
        for tt in TBL_ORDER:
            res = all_tables.get(tt)
            if res:
                unit_info[tt] = {
                    # 返工16修6: 存储值统一为"万元"(normalize 金额字段 v*unit_factor/10000)，
                    # unit_factor/unit_raw 必须反映存储单位，不能写原文"元"误导下游换算错10000倍。
                    "unit_factor": 10000,
                    "unit_raw": "万元",
                    "source_unit_raw": res.get("unit_raw", "元"),  # 原文单位溯源(只进JSON，不进DB)
                    "unit_source_text": res.get("unit_source_text", ""),
                    "sheet_variant": res.get("sheet_variant", "unknown"),  # P0-2
                }

    safe_name = name.replace("/","_").replace(":","").replace(chr(65281),"") if name else sc
    fname = "%s_%s%s_%s.json" % (safe_name, yr, rp, p["type"])
    _out_data = _apply_incremental(os.path.join(OUT, fname), merged_data)
    json.dump({"stock_code":sc,"stock_abbr":name or sc,"report_year":yr,
        "report_period":rp,"report_type":p["type"],"exchange":"SZSE",
        "source":source,"confidence":80,
        "validation":{"pass":v_pass,"total":v_total,"score":v_score,"details":v_results},
        "unit_info":unit_info,
        "low_confidence": low_conf_all,
        "data":_out_data},
        open(os.path.join(OUT,fname),"w",encoding="utf-8"),ensure_ascii=False,indent=2)
    ok += 1

    # ---- 日志: PDF 完成 ----
    logger.record_validation(v_pass, v_total, v_results, v_score)
    logger.finish(success=True, total_fields=sum(
        len(tbl) for tbl in merged_data.values()
    ))

    if (idx+1) % 50 == 0:
        print("  --- 进度: [%d/%d] %ds ok=%d fail=%d skip=%d 字段=%d ---" % (idx+1, len(pdfs), time.time()-start, ok, fail, skip, total_fields))

print("\n完成! %ds ok=%d fail=%d 字段=%d" % (time.time()-start, ok, fail, total_fields))
print("M6: %d/%d (%.0f%%)" % (total_passed, max(total_checks,1), total_passed/max(total_checks,1)*100))
print("")
print("=" * 50)
print("  工作留痕:")
print("    提取日志: %s" % LOG_DIR)
print("    提取结果: %s" % OUT)
print("  查看质量: python data_quality_report.py")
print("  或访问:   http://127.0.0.1:7860/quality")
print("=" * 50)
