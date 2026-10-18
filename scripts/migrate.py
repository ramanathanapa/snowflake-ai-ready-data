"""Apply sql/migrations/*.sql in filename order. Migrations are idempotent (IF NOT EXISTS).
Usage: python scripts/migrate.py --account <ORG-ACCOUNT> --user <USER>
"""
import argparse, pathlib
import snowflake.connector
from cryptography.hazmat.primitives import serialization

p = argparse.ArgumentParser()
p.add_argument("--account", required=True)
p.add_argument("--user", required=True)
p.add_argument("--key", default=str(pathlib.Path.home() / ".snowflake" / "rsa_key.p8"))
a = p.parse_args()

key = serialization.load_pem_private_key(pathlib.Path(a.key).read_bytes(), password=None)
der = key.private_bytes(
    serialization.Encoding.DER,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
conn = snowflake.connector.connect(account=a.account, user=a.user, private_key=der)

for f in sorted((pathlib.Path(__file__).parent.parent / "sql" / "migrations").glob("V*.sql")):
    print("applying", f.name)
    conn.execute_string(f.read_text())
print("done")
