"""Sanity checks on FUND_AI.CURATED. Fails loudly. Usage: python tests/check_curated.py --account X --user Y"""
import argparse, pathlib
import snowflake.connector
from cryptography.hazmat.primitives import serialization as q

p = argparse.ArgumentParser(); p.add_argument("--account", required=True); p.add_argument("--user", required=True)
a = p.parse_args()
k = q.load_pem_private_key((pathlib.Path.home() / ".snowflake" / "rsa_key.p8").read_bytes(), password=None)
der = k.private_bytes(q.Encoding.DER, q.PrivateFormat.PKCS8, q.NoEncryption())
cur = snowflake.connector.connect(account=a.account, user=a.user, private_key=der, role="FUND_ENGINEER",
                                  warehouse="FUND_WH", database="FUND_AI", schema="CURATED").cursor()
one = lambda sql: cur.execute(sql).fetchone()

raw, cur_rows = one("SELECT (SELECT COUNT(*) FROM RAW.NPORT_FUND_REPORTED_HOLDING), (SELECT COUNT(*) FROM FUND_HOLDING)")
assert raw == cur_rows, f"join fan-out: raw {raw} vs curated {cur_rows}"
assert one("SELECT COUNT(*) FROM DIM_FUND WHERE TICKERS IS NULL")[0] == 0, "fund without ticker"
nf, ns = one("SELECT COUNT(*), COUNT(DISTINCT SERIES_ID) FROM FUND_FILING")
assert nf == 3 * ns, f"expected 3 filings per series, got {nf} for {ns}"
assert one("SELECT COUNT_IF(VALUE_USD IS NULL OR REPORT_DATE IS NULL) FROM FUND_HOLDING")[0] == 0, "unparsed value/date"
bad = cur.execute("""SELECT d.TICKERS, h.REPORT_DATE, ROUND(SUM(PCT_NET_ASSETS),1) FROM FUND_HOLDING h JOIN DIM_FUND d USING (SERIES_ID)
    GROUP BY 1,2 HAVING SUM(PCT_NET_ASSETS) NOT BETWEEN 90 AND 135""").fetchall()  # >100 legit for leveraged/derivative funds
print("holdings sum outside 90-135% of net assets:", bad or "none")
n, nul = one("SELECT COUNT(*), COUNT_IF(TOTAL_RETURN_PCT IS NULL) FROM FUND_RETURN_MONTHLY")
assert n > 0 and nul < n, "returns view empty/unparsed"
# a security key that spans many distinct issuers means a placeholder identifier collapsed them (e.g. CUSIP 'N/A')
coll = cur.execute("SELECT SECURITY_KEY, COUNT(DISTINCT ISSUER_NAME) FROM FUND_HOLDING GROUP BY 1 HAVING COUNT(DISTINCT ISSUER_NAME) > 20").fetchall()
assert not coll, f"colliding security keys: {coll}"
# --- prospectus layer ---
assert one("SELECT COUNT(*) FROM DIM_FUND_CLASS c LEFT JOIN FUND_FEES f USING (SERIES_ID, CLASS_ID) WHERE f.CLASS_ID IS NULL")[0] == 0, "class without fees"
lo, hi, nul = one("SELECT MIN(EXPENSE_RATIO_PCT), MAX(EXPENSE_RATIO_PCT), COUNT_IF(EXPENSE_RATIO_PCT IS NULL) FROM FUND_FEES")
assert nul == 0 and 0 <= lo and hi <= 3, f"implausible expense ratios {lo}..{hi}"  # fractions mistaken for percent would be <0.01
assert one("SELECT COUNT(*) FROM (SELECT 1 FROM FUND_ANNUAL_RETURN GROUP BY SERIES_ID, CLASS_ID, CALENDAR_YEAR HAVING COUNT(*) > 1)")[0] == 0, "duplicate annual returns"
assert "iShares" in one("SELECT TEXT FROM FUND_PROSPECTUS_TEXT WHERE TICKERS = 'IVV' AND SECTION = 'ObjectivePrimaryTextBlock' LIMIT 1")[0], "text cleaning corrupted letters"
no_ret = [r[0] for r in cur.execute("SELECT TICKERS FROM DIM_FUND d WHERE NOT EXISTS (SELECT 1 FROM FUND_ANNUAL_RETURN r WHERE r.SERIES_ID = d.SERIES_ID)").fetchall()]
print("funds without calendar-year returns in the source:", no_ret or "none")
print("OK:", raw, "holdings,", nf, "filings,", n, "monthly returns")
