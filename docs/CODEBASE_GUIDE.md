# Codebase guide: how everything fits together

Read this top to bottom once, then use it as a map. Every file name below is a real file in the repo.

## 1. The big picture

```
SEC website (public files)
   │  download + filter to 24 funds          loader/*.py            (runs on your laptop)
   ▼
RAW        exact copies of SEC files, all text columns             sql/migrations V001-V003, V005
   │  type, join, de-duplicate                                      V004, V006, V007
   ▼
CURATED    clean typed views: funds, filings, holdings, fees        (views)
   │  shape for the questions a PM asks                             V008
   ▼
ANALYTICS  summary, overlap, changes, bond profile, cost            (views)
   │  describe in business words                                    app/semantic_model.yaml
   ▼
Cortex Analyst   turns a question into SQL                          (Snowflake service)
   ▼
Streamlit app    chat + dashboards                                  app/streamlit_app.py
```

Infrastructure under all of it (database, schemas, warehouse, roles, cost cap) is created by Terraform in `infra/terraform/`.
Tests in `tests/` assert that each layer is correct.

**Why layers?** Each layer has one job, so a bug has one place to live. When overlap numbers looked wrong, the fault was in CURATED (a placeholder identifier), not in the app. Fixing it there fixed every consumer.

## 2. Concepts you need

### Snowflake
| Term | Meaning here |
|---|---|
| **Account / user** | Your trial account identifier (`ORG-ACCOUNT`) and login name. |
| **Role** | A set of permissions. `FUND_ENGINEER` builds and owns things; `FUND_PM` can only read ANALYTICS and open the app. Everything runs as a role, never "as the user". |
| **Warehouse** | The compute that runs queries and costs credits. `FUND_WH` is XSMALL, suspends after 60 s. No warehouse running = no cost. |
| **Resource monitor** | A credit cap. `FUND_RM` stops the warehouse at 100 credits a month. |
| **Database / schema** | `FUND_AI` is the database; `RAW`, `CURATED`, `ANALYTICS`, `APP` are schemas (folders of objects). |
| **Stage** | A file area inside Snowflake. `SEC_STAGE` holds uploaded data files; `SEMANTIC_STAGE` holds the YAML; `STREAMLIT_STAGE` holds the app code. |
| **PUT / COPY INTO** | `PUT` uploads a local file to a stage. `COPY INTO table` loads staged files into a table. This is the standard bulk-load path. |
| **View** | A saved query. Our CURATED and ANALYTICS layers are views, so they are always current and cost nothing to store. |
| **Key-pair auth** | You prove identity with a private key (`~/.snowflake/rsa_key.p8`), not a password. The public key is registered on your user. |
| **Cortex Analyst** | A Snowflake service that turns a question plus a semantic model into SQL. Works on your trial. |
| **Semantic model** | A YAML file telling Cortex Analyst what tables and columns mean, their units and synonyms. It is the main lever on chat accuracy. |
| **Streamlit in Snowflake** | A Python web app hosted and run inside your account. It runs with the owner role's rights, so users need no table access. |

### SEC fund data
| Term | Meaning |
|---|---|
| **N-PORT** | Monthly report of every holding a US fund owns. Public for the last month of each quarter, about 60 days later. Our holdings come from it. |
| **NPORT-P** | The "periodic" form type (the normal one). |
| **Series** | One fund. `SERIES_ID` like `S000004362` is AGG. |
| **Class** | One share class of a fund, each with its own ticker and fees. VOO and VFIAX are two classes of the same series, so they share holdings. |
| **Accession number** | The ID of one filing. Joins most RAW tables. |
| **CUSIP / ISIN** | Security identifiers. We combine them into `SECURITY_KEY` to recognise the same stock across funds. |
| **DV01** | Dollar change in a bond fund's value for a 1 basis point rate move. Duration = total DV01 / (net assets x 0.0001). |
| **Prospectus (rr1) data** | Yearly fund documents, in XBRL-style facts: fees, calendar-year returns, objectives. |

### Terraform
Declares infrastructure in `.tf` files. `plan` shows what would change; `apply` makes it so. **State** (`terraform.tfstate`) records what Terraform created, so losing it is a real problem. The Snowflake **provider** is the plugin that talks to your account.

## 3. File by file, in the order to read them

### Infrastructure
- **`infra/terraform/main.tf`** creates, in order: the resource monitor, warehouse, database, four schemas, two roles, then all the grants (who can use what). Read the grants last; they look repetitive but each line is one permission. The final blocks give `FUND_PM` read access to ANALYTICS views and usage of Streamlit apps, using **future grants** so objects recreated later stay covered.
- `variables.tf`, `terraform.tfvars.example`: account name, user, key path, credit cap.
- **`scripts/bootstrap.ps1`**: one-time setup. Makes the key pair, prints the `ALTER USER` statement you must run yourself, writes `terraform.tfvars`, runs init and plan (or apply with `-Apply`).

### Loading data (`loader/`)
- **`funds.txt`**: the 24 tickers. The only place the universe is defined.
- **`load_nport.py`** (the most important script). For one quarter:
  1. Downloads `<period>_nport.zip` (about 400 MB) if missing.
  2. Maps tickers to series IDs using the SEC ticker map.
  3. Streams each TSV inside the zip and keeps only rows that belong to the funds. The chain is `SERIES_ID` -> `ACCESSION_NUMBER` -> `HOLDING_ID`, because the big holdings file has no series column and the identifier and debt tables have no accession column.
  4. Writes the filtered files to `data/filtered/`, `PUT`s them to `@SEC_STAGE`, deletes any earlier rows of that period, `COPY INTO`s RAW, and records the load in `LOAD_MANIFEST`. Re-running is safe.
- **`load_rr.py`**: same pattern for prospectus data. Differences: facts first (they decide which filings to keep), then filings.
- **`load_tickers.py`**: loads the ticker-to-class map into `RAW.SEC_FUND_TICKERS`.

### The SQL layers (`sql/migrations/`, applied in order by `scripts/migrate.py`)
A migration is a numbered SQL file; the runner applies them all, and each statement is safe to repeat (`IF NOT EXISTS` or `CREATE OR REPLACE`).

| File | What it adds |
|---|---|
| `V001` | File format `TSV_HEADER`, stage `SEC_STAGE`, table `LOAD_MANIFEST` |
| `V002` | Nine `NPORT_*` RAW tables. Generated from the real file headers, all `STRING`. |
| `V003` | `SEC_FUND_TICKERS` |
| `V004` | **CURATED core**: `DIM_FUND`, `DIM_FUND_CLASS`, `FUND_FILING`, `FUND_HOLDING`, `FUND_RETURN_MONTHLY`, `FUND_RATE_RISK`. This is where text becomes dates and numbers, and where `SECURITY_KEY` and `PRIMARY_TICKER` are defined. |
| `V005` | `RR_SUB`, `RR_NUM`, `RR_TXT` RAW tables for prospectus data |
| `V006` | Prospectus views: `FUND_FEES`, `FUND_ANNUAL_RETURN`, `FUND_TURNOVER`, `FUND_PROSPECTUS_TEXT`. Source fractions become percent. |
| `V007` | Readable code lists (`DIM_ASSET_CATEGORY`, `DIM_ISSUER_TYPE`) and `FUND_POSITION` (one row per security per filing) |
| `V008` | **ANALYTICS**: `LATEST_FILING`, `LATEST_HOLDING`, `FUND_SUMMARY`, `FUND_CLASS_COST`, `FUND_OVERLAP`, `HOLDING_CHANGES`, `FUND_ASSET_MIX`, `FUND_COUNTRY_MIX`, `BOND_PROFILE`, `ANNUAL_RETURNS`, `MONTHLY_RETURNS` |
| `V009` | Stages for the semantic model and the app |

Ideas worth studying in V008:
- **Overlap** = for every security two funds share, take the smaller weight, then sum. 100 means identical portfolios.
- **Holding changes** compare each fund's two latest snapshots. A position's change is measured **relative to the fund's median change**, because index funds' share counts all move together when units are created or redeemed.
- **Duration** uses the absolute total DV01, because Vanguard reports it negative and iShares positive.

### The AI layer
- **`app/semantic_model.yaml`**: one block per ANALYTICS view. For each column: name, expression, type, description, synonyms, sample values. `custom_instructions` are rules for the model (percent units, which view answers which kind of question). `relationships` let it join views on `fund_ticker`. `verified_queries` are known-good question and SQL pairs that anchor behaviour.
- **`scripts/deploy_app.py`** uploads the YAML and app files and runs `CREATE STREAMLIT`.

### The app (`app/streamlit_app.py`)
- `get_session()` picks the right connection: inside Snowflake it uses the active session; locally it builds one from your key.
- `query()` runs SQL and caches the result for 10 minutes, so tabs don't re-query on every click.
- `call_analyst()` sends the chat history to Cortex Analyst (in Snowflake via `_snowflake`, locally via REST) and returns SQL.
- The **Ask** tab keeps the conversation in `st.session_state`, runs the returned SQL, and shows the table, an optional chart and the SQL. The other tabs (Funds, Compare, What changed) are plain queries over ANALYTICS views.
- Streamlit re-runs the whole script on every click, which is why state is kept in `session_state`.

### Tests (`tests/`)
- `check_curated.py`: counts match, no join fan-out, fees plausible, no key collisions.
- `check_analytics.py`: domain truths (IVV and VOO overlap above 90%, AGG duration about 6 years, XLK mostly unchanged).
- `ask_analyst.py`: 10 real PM questions with expected answers, against the semantic model.

These tests are how the four real bugs were found. Keep adding to them.

### Exploring by hand
`sql/explore/agg_walkthrough.sql` follows one fund through every table. Run it in a worksheet.

## 4. Follow one fact all the way
AGG held a US Treasury note (CUSIP `91282CPL9`) at 0.46% of the fund.
1. **SEC zip**: a row in `FUND_REPORTED_HOLDING.tsv` with `HOLDING_ID 167795847`.
2. **Loader**: kept because AGG's `SERIES_ID` was in the list and its accession matched. Uploaded to the stage.
3. **RAW**: the row sits in `NPORT_FUND_REPORTED_HOLDING` as text; the ISIN is in `NPORT_IDENTIFIERS`; coupon and maturity are in `NPORT_DEBT_SECURITY`.
4. **CURATED**: `FUND_HOLDING` joins all three into one typed row (`PCT_NET_ASSETS` 0.4635, `MATURITY_DATE` 2027-11-30, a `SECURITY_KEY`).
5. **ANALYTICS**: it appears in `LATEST_HOLDING`, and counts toward AGG's top-10 concentration and `BOND_PROFILE` coupon and maturity.
6. **Chat**: "top holdings of AGG" becomes SQL over `latest_holding`, and the row comes back with its as-of date.

## 5. Everyday tasks
| I want to... | Do this |
|---|---|
| Add a fund | Add the ticker to `loader/funds.txt`, then rerun `load_tickers.py`, `load_nport.py` for each period and `load_rr.py`. |
| Load a new quarter | `python loader/load_nport.py --period 2026q3 ...` (and the same for `load_rr.py`). Views update on their own. |
| Add a metric | Add it to a V008 view (as a new migration or edit and reapply), then add the column to `semantic_model.yaml` and run `deploy_app.py`. |
| Teach the chat a new question | Add a verified query to the YAML and a case to `ask_analyst.py`. |
| Change infrastructure | Edit `main.tf`, run `terraform plan`, read it, then `apply`. |

## 6. Design choices and their trade-offs
- **Loader runs on your laptop**: a trial account cannot make outbound web calls from Snowflake. On a paid account this could move inside.
- **Views, not tables**: the data is small, so there is no refresh to manage. If volume grew, switch to Dynamic Tables.
- **RAW is all text**: schema changes in SEC files cannot break a load. Typing happens in CURATED where it is easy to fix.
- **Filter before loading**: 24 funds instead of the whole market keeps it inside free credits.
- **Semantic model over free-form SQL generation**: a model that knows units and synonyms is more accurate and auditable.

## 7. Limits to remember
- No prices, no benchmarks, no sector. N-PORT has no sector field.
- Holdings are months old; every fund has its own as-of date.
- Trial account: Cortex text functions are blocked, so there are no written summaries. The trial also expires after 30 days.
- HYG's duration (1.4 years) is lower than the fund's published figure; we show what N-PORT reports.
- ARKK, QQQM and SCHD have no calendar-year returns in the source.

## 8. Exercises (in order)
1. Run `sql/explore/agg_walkthrough.sql` and change `'AGG'` to `'IVV'`.
2. Run `check_curated.py`. Then break something on purpose, for example edit the `N/A` filter in V004, reapply, and see which assertion fails.
3. Ask the app a question the model gets wrong, and fix it with a verified query.
4. Add one ticker to `funds.txt` and load it.
5. Read `main.tf` and change `credit_quota`, run `plan` and read the diff.
