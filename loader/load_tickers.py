"""Load SEC fund ticker map rows for the series behind loader/funds.txt into RAW.SEC_FUND_TICKERS.
Usage: python loader/load_tickers.py --account <ORG-ACCOUNT> --user <USER>
"""
import argparse, json, os, pathlib, sys, urllib.request
import snowflake.connector
from cryptography.hazmat.primitives import serialization

p = argparse.ArgumentParser()
p.add_argument("--account", required=True)
p.add_argument("--user", required=True)
p.add_argument("--key", default=str(pathlib.Path.home() / ".snowflake" / "rsa_key.p8"))
a = p.parse_args()

req = urllib.request.Request("https://www.sec.gov/files/company_tickers_mf.json",
                             headers={"User-Agent": os.environ.get("SEC_USER_AGENT") or sys.exit("Set SEC_USER_AGENT (SEC requires a contact)")})
m = json.load(urllib.request.urlopen(req))
ix = {f: m["fields"].index(f) for f in ("cik", "seriesId", "classId", "symbol")}
tickers = set((pathlib.Path(__file__).parent / "funds.txt").read_text().upper().split())
series = {r[ix["seriesId"]] for r in m["data"] if r[ix["symbol"]].upper() in tickers}
rows = [(str(r[ix["cik"]]), r[ix["seriesId"]], r[ix["classId"]], r[ix["symbol"]])
        for r in m["data"] if r[ix["seriesId"]] in series]

key = serialization.load_pem_private_key(pathlib.Path(a.key).read_bytes(), password=None)
der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
conn = snowflake.connector.connect(account=a.account, user=a.user, private_key=der,
    role="FUND_ENGINEER", warehouse="FUND_WH", database="FUND_AI", schema="RAW")
cur = conn.cursor()
cur.execute("TRUNCATE TABLE SEC_FUND_TICKERS")
cur.executemany("INSERT INTO SEC_FUND_TICKERS (CIK, SERIES_ID, CLASS_ID, SYMBOL) VALUES (%s,%s,%s,%s)", rows)
print(len(rows), "class rows for", len(series), "series")
