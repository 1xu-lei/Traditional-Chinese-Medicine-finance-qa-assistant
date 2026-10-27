# -*- coding: utf-8 -*-
"""
回填 core_performance_indicators_sheet 的营收增长率字段（第一阶段：仅营收，口径无歧义）。

字段（存小数，与 gross_profit_margin 等口径一致）：
  operating_revenue_yoy_growth = (本期营收 - 去年同期营收) / |去年同期营收|   累计同比
  operating_revenue_qoq_growth = (Q3单季营收 - Q2单季营收) / |Q2单季营收|       仅 Q3

口径说明：
  - 营收是"年初至报告期累计值"，同比 = 本期累计 vs 去年同期累计。
  - Q3单季 = Q3累计 - H1累计；Q2单季 = H1累计 - Q1累计。
  - 同比基期为 year-1 同期；2023 的 Q1/H1/Q3 因 2022 仅 FY 而无基期，跳过。
  - 同 (stock_code, report_year, report_period) 若有多行 report_type，取 pdf_extracted 优先。

用法：
  python backfill_derived_fields.py --dry-run    # 只统计预览，不写库
  python backfill_derived_fields.py              # 实际回填（只更新当前为 NULL 的行）
  python backfill_derived_fields.py --force      # 覆盖已有值
"""
import sys
import argparse
sys.stdout.reconfigure(encoding='utf-8')

import yaml
import mysql.connector

FIELDS = ['operating_revenue_yoy_growth', 'operating_revenue_qoq_growth']


def load_db():
    cfg = yaml.safe_load(open('config/config.yaml', encoding='utf-8'))['database']
    return mysql.connector.connect(
        host=cfg['host'], port=cfg['port'], database=cfg['database'],
        user=cfg['user'], password=cfg['password'], use_pure=True,
    )


def _yoy(cur, prev):
    """同比增长率（小数）。分母取绝对值，兼容负基期。"""
    if cur is None or prev is None or prev == 0:
        return None
    return round((float(cur) - float(prev)) / abs(float(prev)), 6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只统计不写库')
    ap.add_argument('--force', action='store_true', help='覆盖已有值（默认只填 NULL）')
    args = ap.parse_args()

    conn = load_db()
    cur = conn.cursor(dictionary=True)

    # 1. income_sheet 营收（含 report_type），折叠成权威映射
    cur.execute("""
        SELECT stock_code, report_year, report_period, report_type, total_operating_revenue
        FROM income_sheet
    """)
    revenue = {}
    for r in cur.fetchall():
        key = (r['stock_code'], r['report_year'], r['report_period'])
        cur_val = revenue.get(key)
        if cur_val is None:
            revenue[key] = (r['report_type'], r['total_operating_revenue'])
        elif cur_val[0] != 'pdf_extracted' and r['report_type'] == 'pdf_extracted':
            revenue[key] = (r['report_type'], r['total_operating_revenue'])
    revenue = {k: v[1] for k, v in revenue.items()}

    # 2. core 表所有行（待回填）
    cur.execute("""
        SELECT serial_number, stock_code, report_year, report_period,
               operating_revenue_yoy_growth, operating_revenue_qoq_growth
        FROM core_performance_indicators_sheet
    """)
    core_rows = cur.fetchall()

    updates = []
    stats = {f: {'calc': 0, 'skip_null_base': 0, 'skip_existing': 0} for f in FIELDS}

    for row in core_rows:
        code, year, period = row['stock_code'], row['report_year'], row['report_period']
        rev = revenue.get((code, year, period))

        new_vals = {}
        # 同比
        prev_rev = revenue.get((code, year - 1, period))
        new_vals['operating_revenue_yoy_growth'] = _yoy(rev, prev_rev)
        # 环比（仅 Q3，需 H1、Q1）
        if period == 'Q3':
            h1 = revenue.get((code, year, 'H1'))
            q1 = revenue.get((code, year, 'Q1'))
            if h1 is not None and q1 is not None and rev is not None:
                q2 = float(h1) - float(q1)
                q3 = float(rev) - float(h1)
                new_vals['operating_revenue_qoq_growth'] = _yoy(q3, q2)

        to_write = {}
        for f in FIELDS:
            v = new_vals.get(f)
            if v is None:
                stats[f]['skip_null_base'] += 1
                continue
            if row[f] is not None and not args.force:
                stats[f]['skip_existing'] += 1
                continue
            stats[f]['calc'] += 1
            to_write[f] = v

        if to_write:
            updates.append((row['serial_number'], to_write))

    print('=' * 70)
    print(f'{"DRY-RUN 预览" if args.dry_run else "实际回填"}  (force={args.force})')
    print('=' * 70)
    print(f'core 表总行数: {len(core_rows)}')
    print(f'income 营收权威映射条目: {len(revenue)}')
    print()
    print('字段回填统计：')
    print(f'{"字段":40s} {"计算成功":>8s} {"基期缺失":>8s} {"已有值跳过":>8s}')
    for f in FIELDS:
        s = stats[f]
        print(f'{f:40s} {s["calc"]:>8d} {s["skip_null_base"]:>8d} {s["skip_existing"]:>8d}')
    print()
    print(f'待更新行数（去重后）: {len(updates)}')

    if args.dry_run:
        print()
        print('样本（前 8 行）：')
        for sn, vals in updates[:8]:
            print(f'  serial={sn} -> {vals}')
        conn.close()
        return

    if not updates:
        print('没有需要更新的行。')
        conn.close()
        return

    set_clause = ', '.join(f'`{c}`=%s' for c in FIELDS)
    sql = f"UPDATE core_performance_indicators_sheet SET {set_clause} WHERE serial_number=%s"
    for sn, vals in updates:
        cur.execute(sql, [vals.get(c) for c in FIELDS] + [sn])
    conn.commit()
    print(f'\n已回填 {len(updates)} 行。')
    conn.close()


if __name__ == '__main__':
    main()
