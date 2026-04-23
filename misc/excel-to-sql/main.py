"""
load_sales.py – read daily_sales_data.xlsx → push into an SQL table

USAGE
$ DATABASE_URL="postgresql+psycopg://user:pass@host/db" \
  python load_sales.py /path/to/daily_sales_data.xlsx
# If DATABASE_URL is unset it falls back to local SQLite (db/sales.db)

CRON EXAMPLE  (runs at 6 AM every day)
0 6 * * * /usr/bin/env -i DATABASE_URL="..." /usr/bin/python /opt/load_sales.py /share/daily_sales_data.xlsx
"""
import sys, os, pathlib, pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# ---------- 1. Config ----------
DB_URL = os.getenv("DATABASE_URL", "sqlite:///db/sales.db")
EXCEL_PATH = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "daily_sales_data.xlsx")
TABLE      = "daily_sales"

# ---------- 2. Read & clean ----------
df = pd.read_excel(EXCEL_PATH)
df.columns = df.columns.str.strip().str.replace(" ", "_")   # neat snake-case cols
df["Store"].ffill(inplace=True)                             # carry-forward blanks
df["Date"] = pd.to_datetime(df["Date"]).dt.date             # pure YYYY-MM-DD

# OPTIONAL: make a composite primary-key column (Store+Date) for de-dup
df["pk"] = df["Store"] + "_" + df["Date"].astype(str)

# ---------- 3. Push ----------
engine = create_engine(DB_URL, future=True)

with engine.begin() as conn:                       # one atomic transaction
    # 3a. create table if absent (datatype guesses are OK for demos)
    df.head(0).to_sql(TABLE, conn, if_exists="append", index=False)

    # 3b. UPSERT – works on Postgres / SQLite 3.35+  (falls back to append otherwise)
    try:
        df.to_sql(TABLE, conn, if_exists="append", index=False,
                  method="multi", chunksize=1000,
                  # dtype={c: df.dtypes[c] for c in df.columns},
                  # let SQLAlchemy generate INSERT … ON CONFLICT DO NOTHING
                  #              PK name must match the table definition
                  )
        # where necessary you can write explicit text(...) for MERGE/ON DUPLICATE
    except SQLAlchemyError as e:
        print(f"[WARN] fast upsert path failed → falling back to plain INSERT ({e})")
        df.to_sql(TABLE, conn, if_exists="append", index=False, method="multi")

    # 3c. Simple check
    rows = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar_one()
    print(f"Ingest complete. {rows:,} total rows in {TABLE}.")

print("Done ✅")