# -*- coding: utf-8 -*-
"""
import_db.py: JSON -> 校验 -> 置信度 -> 单位统一 -> 入库
集成模块：数据导入、单位识别、异常检测、pending_review
"""
import sys, os, json, glob, time
sys.path.insert(0, "D:/python-leanrn/codex")
sys.path.insert(0, "D:/python-leanrn/codex/pdf_extractor/lib")
import pymysql
from pdf_extractor.validator import validate_all
from pdf_extractor.confidence_scorer import P as score_p, F as score_f, V as score_v, C as score_c, calc, decide
from pdf_extractor.field_rules import validate_table_data

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


# ===== 数据库配置 =====
DB = {"host":"127.0.0.1","port":3306,"user":"root","password":_cfg_password(),"database":"finance_data","charset":"utf8mb4"}
TABLES = {"income_sheet","balance_sheet","cash_flow_sheet","core_performance_indicators_sheet"}

# ===== 字段中文名映射 =====
FIELD_CN = {
    "total_operating_revenue": "营业总收入",
    "net_profit": "净利润",
    "total_profit": "利润总额",
    "operating_profit": "营业利润",
    "asset_total_assets": "总资产",
    "liability_total_liabilities": "总负债",
    "equity_total_equity": "净资产",
    "operating_cf_net_amount": "经营现金流净额",
    "investing_cf_net_amount": "投资现金流净额",
    "financing_cf_net_amount": "筹资现金流净额",
}

# ===== 单位倍率 =====
UNIT_FACTOR = {
    "元": 1,
    "千元": 1000,
    "万元": 10000,
    "亿元": 100000000,
}

# ===== 需要检查的字段（用于异常检测） =====
CHECK_FIELDS = {
    "income_sheet": ["total_operating_revenue", "net_profit", "total_profit", "operating_profit"],
    "balance_sheet": ["asset_total_assets", "liability_total_liabilities", "equity_total_equity"],
    "cash_flow_sheet": ["operating_cf_net_amount", "investing_cf_net_amount", "financing_cf_net_amount"],
}


class DataImporter:
    """数据导入器：集成单位识别、异常检测、pending_review"""

    def __init__(self):
        self.conn = pymysql.connect(**DB)
        self.cur = self.conn.cursor()
        self.table_columns = {}
        self.company_to_code = {}
        self._init_db()

    def _init_db(self):
        """初始化数据库连接和表结构"""
        # 获取表结构
        for t in TABLES:
            self.cur.execute("SHOW COLUMNS FROM " + t)
            self.table_columns[t] = {r[0] for r in self.cur.fetchall()
                                     if r[0] not in ("serial_number","stock_code","stock_abbr",
                                                     "report_type","report_period","report_year","created_at")}

        # 获取公司代码映射
        for t in TABLES:
            try:
                self.cur.execute("SELECT DISTINCT stock_code, stock_abbr FROM " + t)
                for r in self.cur.fetchall():
                    if r[1]: self.company_to_code[r[1].replace(" ","")] = r[0]
            except: pass

        # 确保 pending_review 表存在
        self._ensure_pending_review_table()

        # 确保 report_metadata 表存在
        self._ensure_metadata_table()

    def _ensure_pending_review_table(self):
        """确保 pending_review 表存在"""
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_review (
                id INT PRIMARY KEY AUTO_INCREMENT,
                company VARCHAR(100),
                stock_code VARCHAR(10),
                period VARCHAR(20),
                indicator VARCHAR(100),
                table_name VARCHAR(50),
                raw_value DECIMAL(20,4),
                unit_raw VARCHAR(20),
                reason VARCHAR(200),
                source_file VARCHAR(255),
                status VARCHAR(20) DEFAULT 'pending',
                reviewed_at DATETIME,
                review_comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def _ensure_metadata_table(self):
        """确保 report_metadata 表存在（含字段统计列）"""
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS report_metadata (
                id INT AUTO_INCREMENT PRIMARY KEY,
                stock_code VARCHAR(10),
                stock_abbr VARCHAR(50),
                report_year INT,
                report_period VARCHAR(10),
                source_file VARCHAR(200),
                report_unit VARCHAR(50),
                unit_factor DECIMAL(20,4),
                unit_source_text VARCHAR(500),
                extract_confidence DECIMAL(5,2),
                fields_total INT DEFAULT 0,
                fields_valid INT DEFAULT 0,
                fields_flagged INT DEFAULT 0,
                review_status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_report (stock_code, report_year, report_period)
            )
        """)
        # 兼容旧表: 尝试加列
        for col in ["fields_total", "fields_valid", "fields_flagged"]:
            try:
                self.cur.execute("ALTER TABLE report_metadata ADD COLUMN %s INT DEFAULT 0" % col)
            except Exception:
                pass
        self.conn.commit()

    def import_all(self):
        """导入所有JSON文件"""
        jfs = []
        for d in ["D:/python-leanrn/codex/data/extracted/sse","D:/python-leanrn/codex/data/extracted/szse"]:
            if os.path.isdir(d): jfs.extend(glob.glob(os.path.join(d,"*.json")))
        # 返工14: 不再按文件名一刀切过滤摘要版。各表独立导入，无数据的表自然跳过。
        # 旧逻辑 `jfs = [f for f in jfs if "_summary" not in ...]` 误伤了55家季报。

        print("共 %d 个JSON待导入" % len(jfs))

        # 清空旧数据（包括 pending_review 和 report_metadata）
        self.cur.execute("DELETE FROM pending_review")
        self.cur.execute("DELETE FROM report_metadata")
        for t in TABLES:
            self.cur.execute("DELETE FROM " + t + " WHERE report_type LIKE 'pdf_%%'")
        self.conn.commit()

        auto = flagged = rejected = fields = 0
        start = time.time()

        for idx, fp in enumerate(jfs):
            result = self._import_one(fp)
            if result == "auto": auto += 1
            elif result == "flagged": flagged += 1
            elif result == "rejected": rejected += 1
            fields += 1

            if (idx+1) % 100 == 0:
                print("  [%d/%d] auto=%d flag=%d reject=%d" % (idx+1, len(jfs), auto, flagged, rejected))

        print("\n完成! %ds" % (time.time()-start))
        print("自动: %d  待复核: %d  不入库: %d  字段: %d" % (auto, flagged, rejected, fields))

        for t in TABLES:
            self.cur.execute("SELECT COUNT(*) FROM " + t + " WHERE report_type LIKE 'pdf_%%'")
            print("  [%s] %d行" % (t, self.cur.fetchone()[0]))

        # 派生字段计算（事一：公式双轨制）
        print("\n===== 派生字段计算 =====")
        self._compute_derived_fields()

        # M7 累计单调性质量门（事三）
        print("\n===== M7 累计单调性校验 =====")
        self._check_monotonicity()

    def _collect_pdf_reports(self, need_period=False):
        """从四张业务表收集去重后的报告清单，绕开 UNION collation 冲突"""
        reports = {}
        for tbl in TABLES:
            if need_period:
                sql = (f"SELECT DISTINCT stock_code, report_year, report_period, report_type "
                       f"FROM {tbl} WHERE report_type LIKE 'pdf_%%' AND report_period IN ('Q1','H1','Q3','FY')")
            else:
                sql = (f"SELECT DISTINCT stock_code, report_year, report_period, report_type "
                       f"FROM {tbl} WHERE report_type LIKE 'pdf_%%'")
            try:
                self.cur.execute(sql)
                for r in self.cur.fetchall():
                    reports[tuple(r)] = True
            except Exception:
                pass
        return [list(r) for r in reports.keys()]

    def _compute_derived_fields(self):
        """事一: 公式双轨制 — 直取优先 + 公式兜底 + 恒等式校验"""
        from pdf_extractor.derived_fields import DERIVED_FIELDS, compute_derived, check_identity

        reports = self._collect_pdf_reports(need_period=False)

        computed_total = 0
        identity_fixed = 0
        skipped = 0

        for sc, yr, rp, rt in reports:
            # 收集该报告所有字段数据
            db_data = {}
            for tbl in TABLES:
                cols = self.table_columns.get(tbl, set())
                if not cols:
                    continue
                col_str = ", ".join(cols)
                self.cur.execute(
                    f"SELECT {col_str} FROM {tbl} "
                    f"WHERE stock_code=%s AND report_year=%s AND report_period=%s AND report_type LIKE 'pdf_%%'",
                    (sc, yr, rp)
                )
                row = self.cur.fetchone()
                if row:
                    col_list = list(cols)
                    for i, fld in enumerate(col_list):
                        db_data[(tbl, fld)] = row[i]

            if not db_data:
                continue

            # ── 恒等式校验: equity_total_equity ──
            asset = db_data.get(("balance_sheet", "asset_total_assets"))
            liability = db_data.get(("balance_sheet", "liability_total_liabilities"))
            equity_direct = db_data.get(("balance_sheet", "equity_total_equity"))

            passed, equity_computed, detail = check_identity(asset, liability, equity_direct)
            if not passed and equity_computed is not None:
                # 直取不过 → 用 A−L 覆盖
                self._upsert_field(sc, yr, rp, rt, "balance_sheet",
                                   "equity_total_equity", equity_computed)
                identity_fixed += 1

            # ── 公式兜底: 其他派生字段 ──
            for fkey, spec in DERIVED_FIELDS.items():
                if fkey == "equity_total_equity":
                    continue  # 恒等式已单独处理
                tbl = spec["table"]
                # 检查直取是否已存在
                existing = db_data.get((tbl, fkey))
                if existing is not None:
                    continue  # 直取优先

                # 尝试计算
                result, source_info = compute_derived(db_data, fkey)
                if result is not None:
                    self._upsert_field(sc, yr, rp, rt, tbl, fkey, result)
                    computed_total += 1
                else:
                    skipped += 1

        print(f"  派生字段: 计算 {computed_total} 个, 恒等式修复 {identity_fixed} 个, 跳过 {skipped} 个")
        self.conn.commit()

    def _upsert_field(self, sc, yr, rp, rt, tbl, fld, val):
        """更新已有行的单个字段值，无行则INSERT（带stock_abbr保NOT NULL）"""
        try:
            self.cur.execute(
                f"SELECT serial_number, stock_abbr FROM {tbl} "
                f"WHERE stock_code=%s AND report_year=%s AND report_period=%s AND report_type LIKE 'pdf_%%'",
                (sc, yr, rp)
            )
            row = self.cur.fetchone()
            if row:
                self.cur.execute(
                    f"UPDATE {tbl} SET {fld}=%s WHERE serial_number=%s",
                    (val, row[0])
                )
            else:
                # 无现有行：先查公司名，没有就用 stock_code 占位
                abbr = sc
                try:
                    self.cur.execute(
                        f"SELECT stock_abbr FROM {tbl} WHERE stock_code=%s AND stock_abbr IS NOT NULL LIMIT 1",
                        (sc,)
                    )
                    r = self.cur.fetchone()
                    if r and r[0]:
                        abbr = r[0]
                except Exception:
                    pass
                self.cur.execute(
                    f"INSERT INTO {tbl} (stock_code, stock_abbr, report_year, report_period, report_type, {fld}) "
                    f"VALUES (%s, %s, %s, %s, %s, %s)",
                    (sc, abbr, yr, rp, rt, val)
                )
        except Exception as e:
            print(f"  [WARN] _upsert_field {tbl}.{fld} {sc}/{yr}/{rp} 失败: {e}")

    def _check_monotonicity(self):
        """返工8: M7 按字段类型拆分 — 严格单调(营收) + 灵活提示(可负字段)"""
        # 严格单调：必然非负累计字段，断裂 = 错误
        STRICT_CUMULATIVE = [
            ("income_sheet", "total_operating_revenue"),
        ]
        # 可负字段：不做单调判错，改算隐含季度增量，增量异常大才提示
        FLEX_CUMULATIVE = [
            ("income_sheet", "net_profit"),
            ("income_sheet", "total_profit"),
            ("cash_flow_sheet", "operating_cf_net_amount"),
            ("cash_flow_sheet", "investing_cf_net_amount"),
            ("cash_flow_sheet", "financing_cf_net_amount"),
        ]
        all_reports = self._collect_pdf_reports(need_period=True)
        groups = {(sc, yr) for sc, yr, rp, rt in all_reports}

        strict_breaks = 0
        flex_warns = 0

        for sc, yr in groups:
            # ── 严格单调（营收）──
            for tbl, fld in STRICT_CUMULATIVE:
                rows = self._get_period_vals(tbl, fld, sc, yr)
                if len(rows) < 2:
                    continue
                prev_val, prev_rp = None, None
                for rp, val in rows:
                    val = float(val)
                    if prev_val is not None:
                        if val < prev_val * 0.99:
                            strict_breaks += 1
                            self._flag_monotonic(sc, yr, tbl, fld,
                                f"M7: {prev_rp}={prev_val:.2f} → {rp}={val:.2f}")
                    prev_val, prev_rp = val, rp

            # ── 灵活提示（可负字段）──
            for tbl, fld in FLEX_CUMULATIVE:
                rows = self._get_period_vals(tbl, fld, sc, yr)
                if len(rows) < 2:
                    continue
                vals = [float(v) for _, v in rows]
                max_abs = max(max(abs(v) for v in vals), 1.0)
                # 算隐含季度增量
                for i in range(1, len(vals)):
                    inc = vals[i] - vals[i-1]
                    if abs(inc) > max_abs * 5:
                        flex_warns += 1
                        self._flag_monotonic(sc, yr, tbl, fld,
                            f"M7W_implied_increment: {rows[i-1][0]}→{rows[i][0]} inc={inc:.2f} > 5×max|{fld}|={max_abs:.0f}")
                        break  # 一个字段只标记一次

        print(f"  单调性断裂: {strict_breaks} 处（严格，全为营收） + {flex_warns} 处（提示）")
        self.conn.commit()

    def _get_period_vals(self, tbl, fld, sc, yr):
        """查询同公司同年四期字段值，返回 [(period, value), ...]"""
        if fld not in self.table_columns.get(tbl, set()):
            return []
        self.cur.execute(
            f"SELECT report_period, {fld} FROM {tbl} "
            f"WHERE stock_code=%s AND report_year=%s AND report_period IN ('Q1','H1','Q3','FY') "
            f"AND report_type LIKE 'pdf_%%' AND {fld} IS NOT NULL "
            f"ORDER BY FIELD(report_period, 'Q1','H1','Q3','FY')",
            (sc, yr)
        )
        return [(rp, float(v)) for rp, v in self.cur.fetchall() if v is not None]

    def _flag_monotonic(self, sc, yr, tbl, fld, detail):
        """标记单调性断裂到 pending_review"""
        try:
            self.cur.execute("""
                INSERT INTO pending_review (company, stock_code, period, indicator, table_name,
                    raw_value, reason, source_file, status)
                VALUES (%s, %s, %s, %s, %s, 0, %s, '', 'pending')
            """, ('', sc, str(yr), fld, tbl, f'M7_period_monotonic: {detail}'))
        except:
            pass

    def _import_one(self, fp):
        """导入单个JSON文件"""
        # 统一使用 company_code_map 查公司名
        try:
            from pdf_extractor.company_code_map import resolve
            if not name or name.isdigit():
                resolved, _ = resolve(sc)
                if resolved:
                    name = resolved
        except Exception:
            pass

        with open(fp, encoding="utf-8") as f:
            rec = json.load(f)

        sc = str(rec.get("stock_code",""))
        name = rec.get("stock_abbr","")
        yr = rec.get("report_year")
        rp = rec.get("report_period")
        rtype = rec.get("report_type","full")
        tbl_data = rec.get("data",{})
        source = rec.get("source","pdfplumber_standard")
        unit_info = rec.get("unit_info", {})
        p_score = rec.get("confidence", score_p(source))

        if (not sc or sc == "None") and name:
            sc = self.company_to_code.get(name.replace(" ",""), "")
        if not sc or not yr or not rp:
            return "rejected"

        rt = "pdf_summary" if rtype == "summary" else "pdf_extracted"

        # 过滤字段
        for tbl in list(tbl_data.keys()):
            if tbl not in self.table_columns: del tbl_data[tbl]; continue
            for k in list(tbl_data[tbl].keys()):
                if k not in self.table_columns[tbl]: del tbl_data[tbl][k]
            if not tbl_data[tbl]: del tbl_data[tbl]

        if not tbl_data:
            return "rejected"

        # 校验
        p, t, flags = validate_all(tbl_data)
        v_score = score_v(sum(len(v) for v in tbl_data.values()))
        c_score = score_c(p, t)
        conf = calc(p=p_score, f=80, v=v_score, c=c_score)
        decision, _ = decide(conf)

        if decision == "rejected":
            return "rejected"

        # 获取单位信息
        report_unit, unit_factor, unit_source_text = self._extract_unit(unit_info, tbl_data)

        # ---- 字段级校验 (P0 新增) ----
        field_check = validate_table_data(tbl_data)
        if field_check["flagged_fields"] > 0:
            print("  [校验] %d/%d 字段异常:" % (
                field_check["flagged_fields"], field_check["total_fields"]))
            for flag in field_check["flags"]:
                print("    XX %s.%s = %s: %s" % (
                    flag["table"], flag["field"], flag["value"], flag["reason"]))
                # 异常字段写入 pending_review
                self._flag_field(sc, name, yr, rp, flag, report_unit, fp)
                # P0-3: 从 tbl_data 删除，不入主表
                tbl, fld = flag["table"], flag["field"]
                if tbl in tbl_data and fld in tbl_data[tbl]:
                    del tbl_data[tbl][fld]
            # 清理空表
            for tbl in list(tbl_data.keys()):
                if not tbl_data[tbl]:
                    del tbl_data[tbl]

        # 保存 metadata（加字段统计）
        self._save_metadata(sc, name, yr, rp, fp, report_unit, unit_factor,
                           unit_source_text, conf,
                           fields_total=field_check["total_fields"],
                           fields_valid=field_check["valid_fields"],
                           fields_flagged=field_check["flagged_fields"])

        # 入库
        self._save_data(sc, name, yr, rp, rt, tbl_data, report_unit, unit_factor)

        # 异常检测：插入 pending_review
        self._detect_anomaly(sc, name, yr, rp, tbl_data, report_unit, fp)

        if decision == "flagged":
            return "flagged"
        return "auto"

    def _extract_unit(self, unit_info, tbl_data):
        """从 unit_info 提取单位信息"""
        report_unit = "万元"
        unit_factor = 10000
        unit_source_text = ""

        for tt in tbl_data.keys():
            if tt in unit_info:
                report_unit = unit_info[tt].get("unit_raw", "万元")
                unit_factor = unit_info[tt].get("unit_factor", 10000)
                unit_source_text = unit_info[tt].get("unit_source_text", "")
                break

        return report_unit, unit_factor, unit_source_text

    def _save_metadata(self, sc, name, yr, rp, fp, report_unit, unit_factor,
                       unit_source_text, conf, fields_total=0, fields_valid=0, fields_flagged=0):
        """保存 report_metadata"""
        try:
            self.cur.execute("""
                INSERT INTO report_metadata (stock_code, stock_abbr, report_year, report_period,
                    source_file, report_unit, unit_factor, unit_source_text, extract_confidence,
                    fields_total, fields_valid, fields_flagged)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    source_file=VALUES(source_file), report_unit=VALUES(report_unit),
                    unit_factor=VALUES(unit_factor), unit_source_text=VALUES(unit_source_text),
                    extract_confidence=VALUES(extract_confidence),
                    fields_total=VALUES(fields_total), fields_valid=VALUES(fields_valid),
                    fields_flagged=VALUES(fields_flagged)
            """, (sc, name or sc, yr, rp, fp, report_unit, unit_factor, unit_source_text,
                  conf, fields_total, fields_valid, fields_flagged))
        except Exception as e:
            print("  [WARN] metadata save failed: %s" % e)

    def _save_data(self, sc, name, yr, rp, rt, tbl_data, report_unit, unit_factor):
        """保存数据到各表"""
        for tbl, kv in tbl_data.items():
            if not kv: continue
            self.cur.execute("""
                INSERT IGNORE INTO %s (stock_code,stock_abbr,report_year,report_period,report_type)
                VALUES (%%s,%%s,%%s,%%s,%%s)
            """ % tbl, (sc, name or sc, yr, rp, rt))

            for fld, val in kv.items():
                std_val = val
                self.cur.execute("""
                    UPDATE %s SET %s=%%s, report_type=%%s
                    WHERE stock_code=%%s AND report_year=%%s AND report_period=%%s
                """ % (tbl, fld), (std_val, rt, sc, yr, rp))

        self.conn.commit()

    def _flag_field(self, sc, name, yr, rp, flag, report_unit, fp):
        """标记单个异常字段到 pending_review"""
        from pdf_extractor.field_rules import get_rule
        rule = get_rule(flag["field"])
        indicator_cn = rule.get("cn_label", flag["field"])
        try:
            self.cur.execute("""
                INSERT INTO pending_review
                (company, stock_code, period, indicator, table_name, raw_value, unit_raw, reason, source_file)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (name, sc, rp, indicator_cn, flag["table"], flag["value"],
                  report_unit, flag["reason"], fp))
        except Exception as e:
            pass

    def _detect_anomaly(self, sc, name, yr, rp, tbl_data, report_unit, fp):
        """检测异常数据 — 已由 _import_one 中的 field_rules.validate_table_data 处理
        此方法保留但不再重复插入，避免双写"""
        pass

    def review_pending(self):
        """审核 pending_review 表中的记录"""
        self.cur.execute("SELECT COUNT(*) FROM pending_review WHERE status = 'pending'")
        count = self.cur.fetchone()[0]

        if count == 0:
            print("没有待审核记录")
            return

        print("\n待审核记录: %d 条" % count)
        print("=" * 50)

        self.cur.execute("SELECT * FROM pending_review WHERE status = 'pending' ORDER BY id")
        records = self.cur.fetchall()

        for record in records:
            print("\nID: %s" % record[0])
            print("公司: %s" % record[1])
            print("期间: %s" % record[3])
            print("指标: %s" % record[4])
            print("原始值: %s" % record[6])
            print("当前单位: %s" % record[7])
            print("原因: %s" % record[8])
            print("来源: %s" % record[9])
            print("-" * 50)

            print("请选择单位:")
            print("1. 元")
            print("2. 万元")
            print("3. 亿元")
            print("s. 跳过")
            print("q. 退出")

            choice = input("选择: ").strip()

            if choice == 'q':
                break
            elif choice == 's':
                continue
            elif choice in ('1', '2', '3'):
                unit_name, unit_factor = {
                    '1': ('元', 1),
                    '2': ('万元', 10000),
                    '3': ('亿元', 100000000),
                }[choice]
                self._apply_review(record, unit_name, unit_factor)
                print("已审核")
            else:
                print("无效选择")

    def _apply_review(self, record, unit_name, unit_factor):
        """应用审核结果"""
        record_id = record[0]
        company = record[1]
        stock_code = record[2]
        period = record[3]
        indicator_cn = record[4]
        table_name = record[5]
        raw_value = float(record[6])

        # 把中文指标名转换成英文字段名
        indicator_en = None
        for en, cn in FIELD_CN.items():
            if cn == indicator_cn:
                indicator_en = en
                break

        if not indicator_en:
            print("  无法识别指标: %s" % indicator_cn)
            return

        # 计算标准值（万元）
        std_value = raw_value

        # 更新 pending_review 状态
        self.cur.execute("""
            UPDATE pending_review
            SET status = 'approved', unit_raw = %s, reviewed_at = NOW()
            WHERE id = %s
        """, (unit_name, record_id))

        # 更新实际表
        try:
            self.cur.execute("""
                UPDATE %s SET %s = %%s, unit_raw = '万元', unit_std = '万元'
                WHERE stock_code = %%s AND report_period = %%s
            """ % (table_name, indicator_en), (std_value, stock_code, period))
            print("  已更新 %s.%s = %.2f 万元" % (table_name, indicator_cn, std_value))
        except Exception as e:
            print("  更新失败: %s" % e)

        self.conn.commit()

    def close(self):
        """关闭数据库连接"""
        self.cur.close()
        self.conn.close()


def main():
    """主函数"""
    import argparse
    parser = argparse.ArgumentParser(description='数据导入工具')
    parser.add_argument('--import-data', action='store_true', help='导入所有JSON文件')
    parser.add_argument('--review', action='store_true', help='审核 pending_review 表')
    args = parser.parse_args()

    importer = DataImporter()

    try:
        if args.import_data:
            importer.import_all()
        elif args.review:
            importer.review_pending()
        else:
            # 默认执行导入
            importer.import_all()
    finally:
        importer.close()


if __name__ == "__main__":
    main()
