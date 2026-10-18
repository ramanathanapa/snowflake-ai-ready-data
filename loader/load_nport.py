"""Filter one SEC N-PORT quarterly zip to the funds in funds.txt and load it into FUND_AI.RAW.
Usage: python loader/load_nport.py --period 2025q4 --account <ORG-ACCOUNT> --user <USER>
Re-runnable: rows for a period are deleted and reloaded.
"""
import argparse, io, json, os, pathlib, sys, urllib.request, zipfile
import snowflake.connector
from cryptography.hazmat.primitives import serialization

UA = {"User-Agent": os.environ.get("SEC_USER_AGENT") or sys.exit("Set SEC_USER_AGENT, e.g. \"FundAnalysisPoC you@example.com\" (SEC requires a contact)")}
ROOT = pathlib.Path(__file__).parent.parent
BASE = "https://www.sec.gov/files/dera/data/form-n-port-data-sets/"
# (table, key column used for filtering, parent whose kept keys it follows)
TABLES = [
    ("FUND_REPORTED_INFO", "SERIES_ID", None),
    ("SUBMISSION", "ACCESSION_NUMBER", "acc"),
    ("REGISTRANT", "ACCESSION_NUMBER", "acc"),
    ("MONTHLY_TOTAL_RETURN", "ACCESSION_NUMBER", "acc"),
    ("INTEREST_RATE_RISK", "ACCESSION_NUMBER", "acc"),
    ("FUND_VAR_INFO", "ACCESSION_NUMBER", "acc"),
    ("FUND_REPORTED_HOLDING", "ACCESSION_NUMBER", "acc"),
    ("IDENTIFIERS", "HOLDING_ID", "hold"),
    ("DEBT_SECURITY", "HOLDING_ID", "hold"),
]

p = argparse.ArgumentParser()
p.add_argument("--period", required=True)  # e.g. 2025q4
p.add_argument("--account", required=True)
p.add_argument("--user", required=True)
p.add_argument("--key", default=str(pathlib.Path.home() / ".snowflake" / "rsa_key.p8"))
a = p.parse_args()

data = ROOT / "data"; data.mkdir(exist_ok=True)
zpath = data / f"{a.period}_nport.zip"
if not zpath.exists():
    print("downloading", zpath.name)
    with urllib.request.urlopen(urllib.request.Request(BASE + zpath.name, headers=UA)) as r, open(zpath, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)

# 1. tickers -> series ids
tickers = {t.strip().upper() for t in (pathlib.Path(__file__).parent / "funds.txt").read_text().split() if t.strip()}
req = urllib.request.Request("https://www.sec.gov/files/company_tickers_mf.json", headers=UA)
m = json.load(urllib.request.urlopen(req))
i_sym, i_ser = m["fields"].index("symbol"), m["fields"].index("seriesId")
sym2ser = {row[i_sym].upper(): row[i_ser] for row in m["data"]}
series = {sym2ser[t] for t in tickers if t in sym2ser}
print("unresolved tickers:", sorted(t for t in tickers if t not in sym2ser) or "none")

# 2. stream-filter each table
keep = {"acc": set(), "hold": set()}
series_by_acc = {}
out = data / "filtered" / a.period; out.mkdir(parents=True, exist_ok=True)
counts = {}
with zipfile.ZipFile(zpath) as z:
    for table, keycol, parent in TABLES:
        n = 0
        with z.open(table + ".tsv") as raw, open(out / f"{table}.tsv", "w", encoding="utf-8", newline="\n") as w:
            lines = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            header = lines.readline().rstrip("\r\n")
            w.write(header + "\n")
            cols = header.split("\t")
            k = cols.index(keycol)
            acc_i = cols.index("ACCESSION_NUMBER") if "ACCESSION_NUMBER" in cols else None
            hold_i = cols.index("HOLDING_ID") if "HOLDING_ID" in cols else None
            for line in lines:
                f = line.rstrip("\r\n").split("\t")
                if len(f) <= k: continue
                if parent is None:
                    if f[k] not in series: continue
                    keep["acc"].add(f[acc_i]); series_by_acc[f[acc_i]] = f[k]
                elif f[k] not in keep[parent]: continue
                if table == "FUND_REPORTED_HOLDING": keep["hold"].add(f[hold_i])
                w.write(line.rstrip("\r\n") + "\n"); n += 1
        counts[table] = n
        print(f"{table}: {n} rows")
got = set(series_by_acc.values())
print(f"series with a filing in {a.period}: {len(got)}/{len(series)}")

# 3. load
key = serialization.load_pem_private_key(pathlib.Path(a.key).read_bytes(), password=None)
der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
conn = snowflake.connector.connect(account=a.account, user=a.user, private_key=der,
    role="FUND_ENGINEER", warehouse="FUND_WH", database="FUND_AI", schema="RAW")
cur = conn.cursor()
for table, _, _ in TABLES:
    f = out / f"{table}.tsv"
    cols = f.open(encoding="utf-8").readline().rstrip("\n").split("\t")
    cur.execute(f"DELETE FROM NPORT_{table} WHERE _SOURCE_FILE LIKE '%/{a.period}/%'")
    cur.execute(f"PUT 'file://{f.as_posix()}' @SEC_STAGE/nport/{a.period}/ AUTO_COMPRESS=TRUE OVERWRITE=TRUE")
    sel = ", ".join(f"${i+1}" for i in range(len(cols)))
    cur.execute(f"""COPY INTO NPORT_{table} ({', '.join(cols)}, _SOURCE_FILE)
        FROM (SELECT {sel}, METADATA$FILENAME FROM @SEC_STAGE/nport/{a.period}/)
        FILES = ('{table}.tsv.gz') FILE_FORMAT = (FORMAT_NAME = TSV_HEADER) FORCE = TRUE""")
    cur.execute("DELETE FROM LOAD_MANIFEST WHERE source='nport' AND period=%s AND file_name=%s", (a.period, table))
    cur.execute("INSERT INTO LOAD_MANIFEST (source, period, file_name, row_count, status) VALUES ('nport', %s, %s, %s, 'loaded')",
                (a.period, table, counts[table]))
    print("loaded", table)
print("done")
