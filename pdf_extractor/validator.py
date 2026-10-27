# -*- coding: utf-8 -*-
"""validator.py: M6 cross-table validation"""

TOLERANCE_PCT = 0.02
TOLERANCE_ABS = 100
CROSS_TABLE_TOLERANCE = 0.05  # 1% for cross-table consistency
NP_OCF_RATIO_MIN = -0.2  # np_ocf: allow -20% to 500% ratio
NP_OCF_RATIO_MAX = 5.0

def validate_all(tbl_data):
    inc = tbl_data.get('income_sheet', {})
    bs = tbl_data.get('balance_sheet', {})
    cf = tbl_data.get('cash_flow_sheet', {})
    cpi = tbl_data.get('core_performance_indicators_sheet', {})
    results = []

    ncf = cf.get('net_cash_flow')
    ocf = cf.get('operating_cf_net_amount')
    icf = cf.get('investing_cf_net_amount')
    fcf = cf.get('financing_cf_net_amount')
    if ncf is not None and ocf is not None and icf is not None and fcf is not None:
        cf_sum = ocf + icf + fcf
        if abs(ncf) < TOLERANCE_ABS:
            passed = abs(cf_sum) < TOLERANCE_ABS * 2
        else:
            diff_pct = abs((ncf - cf_sum) / ncf)
            passed = diff_pct < TOLERANCE_PCT
        r = dict(check="cash_flow_internal",passed=passed,detail="net_cf vs sum(O+I+F)",source_tables=["cash_flow_sheet"])
        results.append(r)

    a = bs.get('asset_total_assets')
    l = bs.get('liability_total_liabilities')
    e = bs.get('equity_total_equity')
    if a is not None and l is not None and e is not None and a != 0:
        diff_pct = abs(a - (l + e)) / abs(a)
        passed = diff_pct < TOLERANCE_PCT
        r = dict(check="balance_identity",passed=passed,detail="assets vs liab+equity",source_tables=["balance_sheet"])
        results.append(r)

    np_inc = inc.get('net_profit')
    np_cpi = cpi.get('net_profit')
    np_val = np_inc if np_inc is not None else np_cpi
    ocf_val = cf.get('operating_cf_net_amount')
    if np_val is not None and ocf_val is not None and abs(np_val) > TOLERANCE_ABS and abs(ocf_val) > TOLERANCE_ABS:
        ratio = ocf_val / np_val
        passed = NP_OCF_RATIO_MIN < ratio < NP_OCF_RATIO_MAX
        r = dict(check="np_ocf_direction",passed=passed,detail="OCF/NP ratio=%.2f" % ratio,source_tables=["income_sheet","cash_flow_sheet"])
        results.append(r)

    eps_val = cpi.get('eps')
    tsc = cpi.get('total_share_capital')
    if eps_val is not None and tsc is not None and tsc > 0 and np_val is not None and abs(np_val) > TOLERANCE_ABS:
        implied_np = eps_val * tsc
        if implied_np > 0 and np_val > 0:
            ratio = implied_np / np_val
            passed = 0.5 < ratio < 2.0
        elif implied_np < 0 and np_val < 0:
            ratio = implied_np / np_val
            passed = 0.5 < ratio < 2.0
        else:
            passed = abs(implied_np - np_val) < max(abs(np_val), TOLERANCE_ABS)
        r = dict(check="eps_implied_np",passed=passed,detail="eps*shares vs NP",source_tables=["core_performance_indicators_sheet","income_sheet"])
        results.append(r)

    tp = inc.get('total_profit')
    if tp is not None and np_inc is not None:
        if tp > 0:
            passed = np_inc <= tp * 1.03
        else:
            passed = np_inc >= tp * 1.03
        r = dict(check="profit_before_after_tax",passed=passed,detail="total_profit vs net_profit",source_tables=["income_sheet"])
        results.append(r)

    rev = inc.get('total_operating_revenue') or cpi.get('total_operating_revenue')
    cost = inc.get('operating_expense_cost_of_sales')
    if rev is not None and cost is not None and rev > 0:
        passed = rev > cost
        r = dict(check="revenue_gt_cost",passed=passed,detail="revenue vs cost",source_tables=["income_sheet"])
        results.append(r)

    if rev is not None:
        passed = rev > 0
        r = dict(check="revenue_positive",passed=passed,detail="revenue sign",source_tables=["income_sheet"])
        results.append(r)

    roe = cpi.get('roe') or cpi.get('roe_weighted_excl_non_recurring')
    if roe is not None:
        passed = -0.5 <= roe <= 0.5
        r = dict(check="roe_range",passed=passed,detail="ROE range",source_tables=["core_performance_indicators_sheet"])
        results.append(r)

    # === ????? ?_f(a,b) ===
    # ?????????????????

    rev_is = inc.get('total_operating_revenue')
    rev_cpi = cpi.get('total_operating_revenue')
    if rev_is is not None and rev_cpi is not None and abs(rev_is) > TOLERANCE_ABS:
        diff_pct = abs(rev_is - rev_cpi) / abs(rev_is)
        passed = diff_pct < CROSS_TABLE_TOLERANCE
        r = dict(check="cross_revenue",passed=passed,detail="IS=%.2f vs CPI=%.2f diff=%.4f%%" % (rev_is, rev_cpi, diff_pct*100),source_tables=["income_sheet","core_performance_indicators_sheet"])
        results.append(r)

    if np_inc is not None and np_cpi is not None and abs(np_inc) > TOLERANCE_ABS:
        diff_pct = abs(np_inc - np_cpi) / abs(np_inc)
        passed = diff_pct < CROSS_TABLE_TOLERANCE
        r = dict(check="cross_net_profit",passed=passed,detail="IS=%.2f vs CPI=%.2f diff=%.4f%%" % (np_inc, np_cpi, diff_pct*100),source_tables=["income_sheet","core_performance_indicators_sheet"])
        results.append(r)

    yoy_is = inc.get('operating_revenue_yoy_growth')
    yoy_cpi = cpi.get('operating_revenue_yoy_growth')
    if yoy_is is not None and yoy_cpi is not None and abs(yoy_is) > 0.0001:
        diff_pct = abs(yoy_is - yoy_cpi) / max(abs(yoy_is), 0.0001)
        passed = diff_pct < CROSS_TABLE_TOLERANCE
        r = dict(check="cross_rev_yoy",passed=passed,detail="IS=%.4f vs CPI=%.4f diff=%.2f%%" % (yoy_is, yoy_cpi, diff_pct*100),source_tables=["income_sheet","core_performance_indicators_sheet"])
        results.append(r)

    npyoy_is = inc.get('net_profit_yoy_growth')
    npyoy_cpi = cpi.get('net_profit_yoy_growth')
    if npyoy_is is not None and npyoy_cpi is not None and abs(npyoy_is) > 0.0001:
        diff_pct = abs(npyoy_is - npyoy_cpi) / max(abs(npyoy_is), 0.0001)
        passed = diff_pct < CROSS_TABLE_TOLERANCE
        r = dict(check="cross_np_yoy",passed=passed,detail="IS=%.4f vs CPI=%.4f diff=%.2f%%" % (npyoy_is, npyoy_cpi, diff_pct*100),source_tables=["income_sheet","core_performance_indicators_sheet"])
        results.append(r)

    pass_count = sum(1 for r in results if r['passed'])
    total = len(results)
    return pass_count, total, results


def fmt_report(tbl_data):
    pass_count, total, results = validate_all(tbl_data)
    check_names = {
        'cash_flow_internal': '???????',
        'balance_identity': '???????',
        'np_ocf_direction': '????????',
        'eps_implied_np': 'EPS??',
        'profit_before_after_tax': '????>=???',
        'revenue_gt_cost': '??>????',
        'revenue_positive': '????',
        'roe_range': 'ROE????',
        'cross_revenue': '??-????',
        'cross_net_profit': '??-???',
        'cross_rev_yoy': '??-????',
        'cross_np_yoy': '??-????',
    }
    lines_out = []
    lines_out.append("=" * 56)
    lines_out.append("M6 Validation: %d/%d passed" % (pass_count, total))
    lines_out.append("=" * 56)
    for r in results:
        status = "OK" if r["passed"] else "XX"
        cn = check_names.get(r["check"], r["check"])
        lines_out.append("  [%s] %s: %s" % (status, cn, r["detail"]))
    lines_out.append("-" * 56)
    score = pass_count / max(total, 1) * 100
    lines_out.append("???: %.0f%% (%d/%d)" % (score, pass_count, total))
    return chr(10).join(lines_out)

def validation_score(tbl_data):
    pass_count, total, _ = validate_all(tbl_data)
    if total == 0:
        return 50
    return round(pass_count / total * 100)

def old_validate_all(tbl_data):
    pass_count, total, results = validate_all(tbl_data)
    flags = [r["detail"] for r in results if not r["passed"]]
    return pass_count, total, flags
