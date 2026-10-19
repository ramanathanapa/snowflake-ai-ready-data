"""Upload the semantic model and the Streamlit app to Snowflake and (re)create the Streamlit object.
Usage: python scripts/deploy_app.py --account <ORG-ACCOUNT> --user <USER>
"""
import argparse, pathlib
import snowflake.connector
from cryptography.hazmat.primitives import serialization

ROOT = pathlib.Path(__file__).parent.parent
p = argparse.ArgumentParser()
p.add_argument("--account", required=True)
p.add_argument("--user", required=True)
p.add_argument("--key", default=str(pathlib.Path.home() / ".snowflake" / "rsa_key.p8"))
a = p.parse_args()

key = serialization.load_pem_private_key(pathlib.Path(a.key).read_bytes(), password=None)
der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
cur = snowflake.connector.connect(account=a.account, user=a.user, private_key=der, role="FUND_ENGINEER",
                                  warehouse="FUND_WH", database="FUND_AI", schema="APP").cursor()

def put(local: pathlib.Path, stage: str):
    cur.execute(f"PUT 'file://{local.as_posix()}' {stage} AUTO_COMPRESS=FALSE OVERWRITE=TRUE")
    # PUT does not update an internal stage's directory table; Snowsight's file pickers read it
    cur.execute(f"ALTER STAGE {stage.lstrip('@').split('/')[0]} REFRESH")
    print("uploaded", local.name, "->", stage)

put(ROOT / "app" / "semantic_model.yaml", "@SEMANTIC_STAGE")

app = ROOT / "app" / "streamlit_app.py"
if app.exists():
    for f in (app, ROOT / "app" / "snowflake" / "environment.yml"):
        put(f, "@STREAMLIT_STAGE/fund_ai")
    cur.execute("""CREATE OR REPLACE STREAMLIT FUND_AI.APP.FUND_ANALYST
        ROOT_LOCATION = '@FUND_AI.APP.STREAMLIT_STAGE/fund_ai'
        MAIN_FILE = 'streamlit_app.py'
        QUERY_WAREHOUSE = FUND_WH
        TITLE = 'Fund Analyst'""")
    print("streamlit FUND_ANALYST ready")
