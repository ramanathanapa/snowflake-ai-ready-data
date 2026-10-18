# Fund analysis on Snowflake (free-tier PoC)

Public fund data (SEC N-PORT, prospectus fees/returns, prices, FRED) -> Snowflake -> Cortex Analyst -> Streamlit chat for portfolio managers.
Scope: ~25 ETFs/mutual funds. Sized to fit the 30-day Snowflake trial.

## Layout
```
infra/terraform/   roles, warehouse, resource monitor, database/schemas, grants (Terraform)
sql/migrations/    V001-V009: RAW tables and stages, CURATED and ANALYTICS views, app stages
sql/explore/       read-only walkthrough queries (agg_walkthrough.sql)
loader/            SEC downloaders: load_nport.py, load_rr.py, load_tickers.py, funds.txt
app/               Streamlit app (streamlit_app.py), Cortex Analyst semantic model, environment.yml
scripts/           bootstrap.ps1, migrate.py, deploy_app.py
tests/             check_curated.py, check_analytics.py, ask_analyst.py
docs/              KNOWLEDGE_BASE.md (problem, sources, design, ADRs, lineage, app), CODEBASE_GUIDE.md (how it all fits), platform-blueprint.md (local only, git-ignored)
```

## One-time setup

Prerequisites: Windows, PowerShell, Git for Windows (provides `openssl`), a Snowflake trial account.

| # | Step | How |
|---|------|-----|
| 1 | Install Terraform | `winget install Hashicorp.Terraform`, then open a **new** shell |
| 2 | Get account identifier | Snowsight > account menu > *Copy account identifier* -> `ORG-ACCOUNT` (e.g. `ABCDEFG-XY12345`) |
| 3 | Get login name | Worksheet: `SELECT CURRENT_USER();` |
| 4 | Run the bootstrap | `./scripts/bootstrap.ps1 -Org <ORG> -Account <ACCOUNT> -User <USER>` (generates key pair, writes `terraform.tfvars`, runs `init` + `plan`) |
| 5 | **Manual:** register public key | The script prints an `ALTER USER ... SET RSA_PUBLIC_KEY='...'` statement. Run it once in a Snowsight worksheet. Cannot be scripted: it needs an authenticated session. |
| 6 | Re-run step 4 | Plan should now succeed (`JWT token is invalid` = key not registered, or wrong user/account) |
| 7 | Apply | `./scripts/bootstrap.ps1 ... -Apply` (prompts for `yes`) |

Everything except step 5 (and installing Terraform) is scripted.

## What Terraform creates (23 resources)
- `FUND_RM` resource monitor: 100 credits/month, notify 50/80%, suspend at 100%
- `FUND_WH` warehouse: XSMALL, auto-suspend 60s
- `FUND_AI` database; schemas `RAW`, `CURATED`, `ANALYTICS`, `APP`
- Roles `FUND_ENGINEER` (all on schemas, granted to your user) and `FUND_PM` (usage on `ANALYTICS`, `APP`)
- `FUND_PM` grants: `SELECT` on all and future `ANALYTICS` views, `USAGE` on future Streamlit apps in `APP`
- Known gap (platform check P04-02): `FUND_ENGINEER` and `FUND_PM` are not yet granted to `SYSADMIN`

## Notes
- Private key lives at `~/.snowflake/rsa_key.p8`. Never commit it (git-ignored: `*.p8`, `*.tfvars`, `*.tfstate*`).
- Terraform state is local (`infra/terraform/terraform.tfstate`). Back it up; move to a remote backend if more than one person works on this.
- Provider runs as `ACCOUNTADMIN` for bootstrap only. Tighten to a least-privilege role later.
- Provider is pinned `~> 2.0`; 2.x has had breaking renames, so pin exactly and read release notes before upgrading.
- Trial accounts do not support external access integrations, so ingestion runs from a local loader (`PUT` to stage), not from inside Snowflake.
- Costs: trial gives ~$400 credits for 30 days. Check usage under Admin > Cost Management.

## Migrations
```powershell
pip install snowflake-connector-python cryptography   # one-time (Python 3.12: winget install Python.Python.3.12)
python scripts/migrate.py --account <ORG-ACCOUNT> --user <USER>
```
Applies `sql/migrations/V*.sql` in order; all DDL is idempotent. `V002` was generated from the headers of `2025q4_nport.zip` (kept in git-ignored `data/`).

## Loader
```powershell
$env:SEC_USER_AGENT = "FundAnalysisPoC you@example.com"   # SEC requires a contact; all loaders read it
python loader/load_nport.py --period 2025q4 --account <ORG-ACCOUNT> --user <USER>
```
Downloads the quarter's N-PORT zip (~400 MB, cached in `data/`), keeps only the funds in `loader/funds.txt`, and loads them into `FUND_AI.RAW.NPORT_*`. Re-runnable per period. Notes: SPY and QQQ are unit investment trusts that do not file N-PORT, so the list uses IVV and QQQM; VOO and VFIAX are two classes of one series, so they share rows.

## Curated layer
`sql/migrations/V003-V004` + `python loader/load_tickers.py ...` build typed views in `FUND_AI.CURATED`:
`DIM_FUND`, `DIM_FUND_CLASS`, `FUND_FILING`, `FUND_HOLDING`, `FUND_RETURN_MONTHLY`, `FUND_RATE_RISK`.
Check with `python tests/check_curated.py --account <ORG-ACCOUNT> --user <USER>`.
Notes: `VALUE_USD` is already USD (verified on INR/JPY/HKD rows). `REPORT_DATE` is the portfolio as-of date and differs per fund; `FILING_DATE` is when it became public. Leveraged and derivative funds can exceed 100% of net assets.
On Windows, the Store `python` alias can shadow the real one; call `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` directly.

## Prospectus layer (fees, annual returns, objective/strategy text)
```powershell
python loader/load_rr.py --periods 2025q3 2025q4 2026q1 2026q2 --account <ORG-ACCOUNT> --user <USER>
```
Loads SEC "Mutual Fund Prospectus Risk/Return Summary" (rr1) quarters into `RAW.RR_SUB/NUM/TXT`, filtered to the tracked funds. `V005` (RAW) and `V006` (CURATED) add `FUND_FEES`, `FUND_ANNUAL_RETURN`, `FUND_TURNOVER`, `FUND_PROSPECTUS_TEXT`.
- Funds file a prospectus about once a year, so use 4 quarters to catch every fund; views keep the latest filing per fund/class. Fees verified against known values (IVV/VOO 0.03%, ARKK 0.75%).
- Source gaps: no 1/5/10-year or benchmark returns in this extraction; ARKK, QQQM and SCHD have no calendar-year returns (use `FUND_RETURN_MONTHLY`).
- Fee/return columns are percent (source fractions x 100).

## Analytics layer (`FUND_AI.ANALYTICS`, migrations V007-V008)
PM-facing views; the semantic view / Cortex Analyst reads only these. `FUND_PM` has SELECT on them via Terraform (future + all grants).
| View | Question it answers |
|---|---|
| `FUND_SUMMARY` | One-row fund card: net assets, as-of date, type, positions, top-10 concentration, expense ratio, turnover, last full-year return |
| `LATEST_HOLDING` / `LATEST_FILING` | Positions and snapshot of each fund's newest filing (with `DAYS_SINCE_AS_OF`) |
| `FUND_OVERLAP` | How much two funds' portfolios coincide (sum of smaller weights; both directions) |
| `HOLDING_CHANGES` | NEW / SOLD / INCREASED / DECREASED vs previous snapshot, **relative to the fund's median share-count change** (index funds' share counts all move with creations/redemptions) |
| `FUND_ASSET_MIX`, `FUND_COUNTRY_MIX` | Mix by asset class / country |
| `BOND_PROFILE` | Coupon, maturity, treasury/corporate/securitized split, effective duration (= abs total DV01 / (net assets x 1bp)) |
| `FUND_CLASS_COST` | Share-class cost comparison |
| `ANNUAL_RETURNS`, `MONTHLY_RETURNS` | Performance with fund names |

Known caveats: no sector (N-PORT has no GICS); DV01 sign differs by filer (Vanguard negative), HYG duration from N-PORT (1.4y) is lower than the fund's published figure; "latest" is the latest filing *loaded*, and as-of dates differ per fund.
`python tests/check_analytics.py --account <ORG-ACCOUNT> --user <USER>` asserts domain expectations (IVV~VOO overlap >90%, AGG duration ~6y, XLK mostly unchanged, ...).

## Streamlit app (Fund Analyst) and Cortex Analyst chat
- `app/semantic_model.yaml`: semantic model for Cortex Analyst over the ANALYTICS views (descriptions, synonyms, percent-unit rules, 4 verified queries).
- `app/streamlit_app.py`: tabs Ask (chat), Funds (cards + detail), Compare (overlap heatmap), What changed. Runs in Snowflake and locally.
- Deploy: `python scripts/deploy_app.py --account <ORG-ACCOUNT> --user <USER>` (uploads files, creates `FUND_AI.APP.FUND_ANALYST`). `FUND_PM` gets USAGE via Terraform (future grant).
- Open in Snowsight: Projects > Streamlit > FUND_ANALYST (database FUND_AI, schema APP).
- Run locally: `FUND_ACCOUNT=<ORG-ACCOUNT> FUND_USER=<USER> python -m streamlit run app/streamlit_app.py`.
- Test the semantic model: `python tests/ask_analyst.py --account <ORG-ACCOUNT> --user <USER>` (10 PM questions with expected answers).
- Trial-account limits: Cortex Analyst works, but `CORTEX.COMPLETE` (LLM text) is blocked, so the chat returns SQL + tables, not narrative summaries; Cortex Search is untested. The app pins `streamlit=1.35.0` (Snowflake runtime), so it uses `use_container_width`, which newer local Streamlit versions warn about.

## Claude skills used by this project
The Snowflake skills developed alongside this project are published separately at https://github.com/ramanathanapa/snowflake-skills:
- `snowflake-data-engineering`: building and reviewing pipelines, models and apps (scoped reviews with a fixed report format).
- `snowflake-platform-engineering`: blueprint-driven platform setup with 60 read-only readiness checks.

Install with `claude plugin marketplace add ramanathanapa/snowflake-skills`, then `claude plugin install <plugin>@snowflake-skills`. 

## Platform readiness
A blueprint of platform decisions (`docs/platform-blueprint.md`) and the results of the 60 read-only checks from the `snowflake-platform-engineering` skill are kept **locally, out of git**, because they describe this account's security posture.
Re-run: execute the skill's `checks/*.sql` scripts and `checks/99-scorecard.sql` in one session.

## License
[MIT](LICENSE)
