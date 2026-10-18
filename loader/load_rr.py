"""Filter SEC prospectus risk/return (rr1) quarterly zips to the tracked funds and load into FUND_AI.RAW.RR_*.
Usage: python loader/load_rr.py --periods 2025q3 2025q4 2026q1 2026q2 --account X --user Y
Re-runnable per period (rows for the period are deleted and reloaded).
"""
import argparse, io, os, pathlib, sys, urllib.request, zipfile, json
import snowflake.connector
from cryptography.hazmat.primitives import serialization

UA = {"User-Agent": os.environ.get("SEC_USER_AGENT") or sys.exit("Set SEC_USER_AGENT, e.g. \"FundAnalysisPoC you@example.com\" (SEC requires a contact)")}
ROOT = pathlib.Path(__file__).parent.parent
BASE = "https://www.sec.gov/files/dera/data/mutual-fund-prospectus-risk/return-summary-data-sets/"

p = argparse.ArgumentParser()
p.add_argument("--periods", nargs="+", required=True)
p.add_argument("--account", required=True)
p.add_argument("--user", required=True)
p.add_argument("--key", default=str(pathlib.Path.home() / ".snowflake" / "rsa_key.p8"))
a = p.parse_args()

key = serialization.load_pem_private_key(pathlib.Path(a.key).read_bytes(), password=None)
der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
conn = snowflake.connector.connect(account=a.account, user=a.user, private_key=der,
    role="FUND_ENGINEER", warehouse="FUND_WH", database="FUND_AI", schema="RAW")
cur = conn.cursor()
series = {r[0] for r in cur.execute("SELECT DISTINCT SERIES_ID FROM SEC_FUND_TICKERS")}  # tracked series

def filtered(z, name, keep_series=None, keep_adsh=None):
    """yield (header, lines) of rows whose series/adsh is wanted"""
    with z.open(name + ".tsv") as raw:
        lines = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        header = lines.readline().rstrip("\r\n"); cols = header.split("\t")
        si = cols.index("series") if keep_series is not None else None
        ai = cols.index("adsh")
        yield header
        for line in lines:
            f = line.rstrip("\r\n").split("\t")
            if len(f) != len(cols): continue
            if keep_series is not None and f[si] not in keep_series: continue
            if keep_adsh is not None and f[ai] not in keep_adsh: continue
            yield line.rstrip("\r\n")

for period in a.periods:
    zpath = ROOT / "data" / f"{period}_rr1.zip"
    if not zpath.exists():
        print("downloading", zpath.name)
        with urllib.request.urlopen(urllib.request.Request(BASE + zpath.name, headers=UA)) as r, open(zpath, "wb") as f:
            while chunk := r.read(1 << 20): f.write(chunk)
    out = ROOT / "data" / "filtered" / f"rr_{period}"; out.mkdir(parents=True, exist_ok=True)
    counts, adsh = {}, set()
    with zipfile.ZipFile(zpath) as z:
        for name in ("num", "txt"):                      # facts first: they decide which filings we keep
            with open(out / f"{name}.tsv", "w", encoding="utf-8", newline="\n") as w:
                it = filtered(z, name, keep_series=series); header = next(it); w.write(header + "\n")
                ai = header.split("\t").index("adsh"); n = 0
                for line in it: w.write(line + "\n"); adsh.add(line.split("\t")[ai]); n += 1
            counts[name] = n
        with open(out / "sub.tsv", "w", encoding="utf-8", newline="\n") as w:
            it = filtered(z, "sub", keep_adsh=adsh); w.write(next(it) + "\n"); n = 0
            for line in it: w.write(line + "\n"); n += 1
        counts["sub"] = n
    print(period, counts)
    for name in ("sub", "num", "txt"):
        f = out / f"{name}.tsv"; table = "RR_" + name.upper()
        cols = [c.upper() for c in f.open(encoding="utf-8").readline().rstrip("\n").split("\t")]
        cur.execute(f"DELETE FROM {table} WHERE _SOURCE_FILE LIKE '%/{period}/%'")
        cur.execute(f"PUT 'file://{f.as_posix()}' @SEC_STAGE/rr/{period}/ AUTO_COMPRESS=TRUE OVERWRITE=TRUE")
        sel = ", ".join(f"${i+1}" for i in range(len(cols)))
        cur.execute(f"""COPY INTO {table} ({', '.join(cols)}, _SOURCE_FILE)
            FROM (SELECT {sel}, METADATA$FILENAME FROM @SEC_STAGE/rr/{period}/)
            FILES = ('{name}.tsv.gz') FILE_FORMAT = (FORMAT_NAME = TSV_HEADER) FORCE = TRUE""")
        cur.execute("DELETE FROM LOAD_MANIFEST WHERE source='rr' AND period=%s AND file_name=%s", (period, name))
        cur.execute("INSERT INTO LOAD_MANIFEST (source, period, file_name, row_count, status) VALUES ('rr', %s, %s, %s, 'loaded')",
                    (period, name, counts[name]))
print("done")
