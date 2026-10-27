import pandas as pd
import mysql.connector
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


conn = mysql.connector.connect(
    host="localhost", port=3306,
    user="root", password=_cfg_password(),
    database="finance_data"
)

tables = [
    "income_sheet",
    "balance_sheet",
    "cash_flow_sheet",
    "core_performance_indicators_sheet"
]

outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(outdir, exist_ok=True)

for tbl in tables:
    sql = "SELECT * FROM " + tbl + " WHERE report_type LIKE \"pdf%\""
    df = pd.read_sql(sql, conn)
    path = os.path.join(outdir, tbl + ".xlsx")
    df.to_excel(path, index=False)
    print(f"{tbl}: {len(df)} rows -> {path}")

conn.close()
print("\nDone!")
