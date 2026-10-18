"""Sanity checks on FUND_AI.ANALYTICS with domain expectations. Usage: python tests/check_analytics.py --account X --user Y"""
import argparse, pathlib
import snowflake.connector
from cryptography.hazmat.primitives import serialization as q

p = argparse.ArgumentParser(); p.add_argument("--account", required=True); p.add_argument("--user", required=True)
a = p.parse_args()
k = q.load_pem_private_key((pathlib.Path.home() / ".snowflake" / "rsa_key.p8").read_bytes(), password=None)
der = k.private_bytes(q.Encoding.DER, q.PrivateFormat.PKCS8, q.NoEncryption())
cur = snowflake.connector.connect(account=a.account, user=a.user, private_key=der, role="FUND_ENGINEER",
                                  warehouse="FUND_WH", database="FUND_AI", schema="ANALYTICS").cursor()
one = lambda sql: cur.execute(sql).fetchone()
ov = lambda x, y: one(f"SELECT OVERLAP_PCT FROM FUND_OVERLAP WHERE FUND_A='{x}' AND FUND_B='{y}'")[0]

n, nt = one("SELECT COUNT(*), COUNT(DISTINCT FUND_TICKER) FROM FUND_SUMMARY")
assert n == nt == 24, f"expected 24 distinct funds, got {n}/{nt}"
assert one("SELECT COUNT_IF(EXPENSE_RATIO_PCT IS NULL OR NET_ASSETS IS NULL OR TOP10_CONCENTRATION_PCT IS NULL) FROM FUND_SUMMARY")[0] == 0, "summary has gaps"
assert one("SELECT COUNT(*) FROM FUND_SUMMARY WHERE FUND_TICKER IN ('VOO','BND','VTI','DODGX')")[0] == 4, "primary ticker choice"

# overlap: same index => ~full; equity vs bonds => ~none; symmetric
assert ov("IVV", "VOO") > 90, f"S&P 500 funds should overlap >90%, got {ov('IVV','VOO')}"
assert abs(ov("IVV", "VOO") - ov("VOO", "IVV")) < 1e-6, "overlap not symmetric"  # tolerance: float sum order
assert ov("IVV", "VTI") > 60, "S&P 500 vs total market"
assert one("SELECT COALESCE(MAX(OVERLAP_PCT), 0) FROM FUND_OVERLAP WHERE FUND_A='IVV' AND FUND_B='AGG'")[0] < 5, "equity vs bond overlap"

# bond profile: only bond funds; AGG duration ~6y
bond = {r[0] for r in cur.execute("SELECT FUND_TICKER FROM BOND_PROFILE").fetchall()}
assert {"AGG", "BND", "TLT", "LQD", "HYG", "TIP"} <= bond and not ({"IVV", "XLK", "QQQM"} & bond), f"bond funds: {sorted(bond)}"
dur = dict(cur.execute("SELECT FUND_TICKER, EFFECTIVE_DURATION_YEARS FROM BOND_PROFILE").fetchall())
assert 4 < dur["AGG"] < 8, f"AGG duration {dur['AGG']}"
assert all(v > 0 for v in dur.values()), f"non-positive duration (DV01 sign?): {dur}"
assert 4 < dur["BND"] < 8, f"BND duration {dur['BND']}"
assert dur["TLT"] > dur["AGG"] > 0, "TLT (20y+) must be longer duration than AGG"

# changes: every fund has two snapshots; labels valid
assert one("SELECT COUNT(DISTINCT FUND_TICKER) FROM HOLDING_CHANGES")[0] == 24, "funds missing from HOLDING_CHANGES"
bad = one("SELECT COUNT(*) FROM HOLDING_CHANGES WHERE CHANGE_TYPE NOT IN ('NEW','SOLD','INCREASED','DECREASED','UNCHANGED')")[0]
assert bad == 0
# flow adjustment: an index fund should be mostly UNCHANGED, an active fund (ARKK) should trade
mix = lambda f: dict(cur.execute(f"SELECT CHANGE_TYPE, COUNT(*) FROM HOLDING_CHANGES WHERE FUND_TICKER='{f}' GROUP BY 1").fetchall())
xlk, arkk = mix("XLK"), mix("ARKK")
assert xlk.get("UNCHANGED", 0) / sum(xlk.values()) > 0.4, f"XLK should be mostly unchanged after flow adjustment: {xlk}"
assert arkk.get("UNCHANGED", 0) / sum(arkk.values()) < 0.2, f"ARKK is active and should show trading: {arkk}"
print("duration:", {t: round(v, 2) for t, v in dur.items()})
print("OK analytics checks passed")
