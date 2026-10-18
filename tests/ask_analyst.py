"""Ask Cortex Analyst a battery of PM questions against the semantic model and check the SQL results.
Usage: python tests/ask_analyst.py --account X --user Y [--only substring]"""
import argparse, json, pathlib, urllib.request
import snowflake.connector
from cryptography.hazmat.primitives import serialization as q

p = argparse.ArgumentParser(); p.add_argument("--account", required=True); p.add_argument("--user", required=True)
p.add_argument("--only", default="")
a = p.parse_args()
k = q.load_pem_private_key((pathlib.Path.home() / ".snowflake" / "rsa_key.p8").read_bytes(), password=None)
der = k.private_bytes(q.Encoding.DER, q.PrivateFormat.PKCS8, q.NoEncryption())
conn = snowflake.connector.connect(account=a.account, user=a.user, private_key=der, role="FUND_ENGINEER",
                                   warehouse="FUND_WH", database="FUND_AI", schema="ANALYTICS")
cur = conn.cursor()

def ask(question):
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
            "semantic_model_file": "@FUND_AI.APP.SEMANTIC_STAGE/semantic_model.yaml"}
    req = urllib.request.Request(f"https://{conn.host}/api/v2/cortex/analyst/message", data=json.dumps(body).encode(),
        headers={"Authorization": f'Snowflake Token="{conn.rest.token}"', "Content-Type": "application/json"})
    msg = json.load(urllib.request.urlopen(req, timeout=120))["message"]["content"]
    sql = next((c["statement"] for c in msg if c["type"] == "sql"), None)
    note = next((c["text"] for c in msg if c["type"] == "text"), "")
    return sql, note

import datetime, decimal
def has(rows, lo, hi):
    """any numeric (non-date) cell in the first row lies in [lo, hi]"""
    return any(lo <= float(v) <= hi for v in rows[0]
               if isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, (bool, datetime.date)))

# (question, check on result rows -> bool, what we expect)
CASES = [
    ("Which fund has the highest net assets?", lambda r: r[0][0] == "VTI", "VTI"),
    ("How much do IVV and VOO overlap?", lambda r: has(r, 90, 100), "~97%"),
    ("What is the expense ratio of VFIAX?", lambda r: has(r, 0.035, 0.045), "0.04"),
    ("Which bond fund has the longest duration?", lambda r: r[0][0] == "TLT", "TLT"),
    ("What are the top 5 holdings of AGG?", lambda r: len(r) == 5, "5 rows"),
    ("Which funds hold NVIDIA?", lambda r: len(r) >= 5, ">=5 funds"),
    ("What are the 3 cheapest equity funds by expense ratio?", lambda r: len(r) == 3 and all(has([x], 0, 0.03) for x in r), "3 rows <=0.03"),
    ("What did ARKK buy or sell recently?", lambda r: len(r) > 5, "several changes"),
    ("What percent of QQQM is in the top 10 holdings?", lambda r: has(r, 30, 80), "30-80"),
    ("What is the average coupon of LQD?", lambda r: has(r, 3, 6), "3-6"),
]
bad = 0
for qn, check, expect in CASES:
    if a.only.lower() not in qn.lower(): continue
    try:
        sql, note = ask(qn)
        if not sql: print(f"[NO SQL] {qn}\n   {note[:150]}"); bad += 1; continue
        rows = cur.execute(sql).fetchall()
        ok = bool(rows) and check(rows)
    except Exception as e:
        ok, rows, sql = False, [], f"ERR {str(e)[:200]}"
    bad += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {qn}  (expect {expect})\n   rows={len(rows)} first={rows[:1]}")
    if not ok: print("   SQL:", " ".join(str(sql).split())[:400])
print(f"\n{len(CASES) - bad if not a.only else '-'} passed, {bad} failed")
