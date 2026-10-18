-- Walk-through of one fund (AGG) across every table. Run top to bottom in a Snowsight worksheet.
USE ROLE FUND_ENGINEER;
USE WAREHOUSE FUND_WH;
USE DATABASE FUND_AI;

-- ===== 0. Pick the keys (change 'AGG' to explore another fund) =====
SET series_id = (SELECT SERIES_ID FROM CURATED.DIM_FUND_CLASS WHERE TICKER = 'AGG' LIMIT 1);
SET class_id  = (SELECT CLASS_ID  FROM CURATED.DIM_FUND_CLASS WHERE TICKER = 'AGG' LIMIT 1);
SET acc       = (SELECT ACCESSION_NUMBER FROM CURATED.FUND_FILING
                 WHERE SERIES_ID = $series_id ORDER BY REPORT_DATE DESC LIMIT 1);   -- latest filing
-- one bond position that has an ISIN and debt terms
SET hid       = (SELECT h.HOLDING_ID FROM RAW.NPORT_FUND_REPORTED_HOLDING h
                 JOIN RAW.NPORT_DEBT_SECURITY d USING (HOLDING_ID)
                 JOIN RAW.NPORT_IDENTIFIERS  i USING (HOLDING_ID)
                 WHERE h.ACCESSION_NUMBER = $acc AND h.ASSET_CAT = 'DBT' AND i.IDENTIFIER_ISIN IS NOT NULL
                 ORDER BY TRY_TO_DOUBLE(h.PERCENTAGE) DESC LIMIT 1);
SELECT $series_id AS series_id, $class_id AS class_id, $acc AS accession_number, $hid AS holding_id;

-- ===== 1. RAW layer: one query per table =====
SELECT * FROM RAW.NPORT_SUBMISSION          WHERE ACCESSION_NUMBER = $acc;   -- the filing: dates, type
SELECT * FROM RAW.NPORT_REGISTRANT          WHERE ACCESSION_NUMBER = $acc;   -- the fund company
SELECT * FROM RAW.NPORT_FUND_REPORTED_INFO  WHERE ACCESSION_NUMBER = $acc;   -- fund-level totals, flows, credit spreads (49 cols)
SELECT * FROM RAW.NPORT_FUND_REPORTED_HOLDING WHERE HOLDING_ID = $hid;       -- one position
SELECT * FROM RAW.NPORT_IDENTIFIERS         WHERE HOLDING_ID = $hid;         -- its ISIN (may be several rows)
SELECT * FROM RAW.NPORT_DEBT_SECURITY       WHERE HOLDING_ID = $hid;         -- coupon, maturity
SELECT * FROM RAW.NPORT_MONTHLY_TOTAL_RETURN WHERE ACCESSION_NUMBER = $acc;  -- 3 monthly returns per share class
SELECT * FROM RAW.NPORT_INTEREST_RATE_RISK  WHERE ACCESSION_NUMBER = $acc;   -- DV01 / DV100 by tenor
SELECT * FROM RAW.NPORT_FUND_VAR_INFO       WHERE ACCESSION_NUMBER = $acc;   -- empty for AGG (no VaR index reported)
SELECT * FROM RAW.SEC_FUND_TICKERS          WHERE SERIES_ID = $series_id;    -- ticker <-> series <-> class
SELECT * FROM RAW.LOAD_MANIFEST ORDER BY LOADED_AT DESC;                     -- audit: what was loaded and when

-- ===== 2. CURATED layer: same facts, typed and joined =====
SELECT * FROM CURATED.DIM_FUND            WHERE SERIES_ID = $series_id;
SELECT * FROM CURATED.DIM_FUND_CLASS      WHERE SERIES_ID = $series_id;
SELECT * FROM CURATED.FUND_FILING         WHERE SERIES_ID = $series_id ORDER BY REPORT_DATE;   -- 3 snapshots
SELECT * FROM CURATED.FUND_HOLDING        WHERE HOLDING_ID = $hid;                              -- RAW holding + ISIN + debt terms
SELECT * FROM CURATED.FUND_RETURN_MONTHLY WHERE CLASS_ID = $class_id ORDER BY MONTH_END;
SELECT * FROM CURATED.FUND_RATE_RISK      WHERE SERIES_ID = $series_id ORDER BY REPORT_DATE;

-- ===== 3. Seeing the joins yourself =====
-- 3a. How one filing connects to its holdings
SELECT s.ACCESSION_NUMBER, s.REPORT_DATE, s.FILING_DATE, i.SERIES_NAME, i.NET_ASSETS,
       (SELECT COUNT(*) FROM RAW.NPORT_FUND_REPORTED_HOLDING h WHERE h.ACCESSION_NUMBER = s.ACCESSION_NUMBER) AS holdings
FROM RAW.NPORT_SUBMISSION s
JOIN RAW.NPORT_FUND_REPORTED_INFO i USING (ACCESSION_NUMBER)
WHERE i.SERIES_ID = $series_id
ORDER BY s.REPORT_DATE;

-- 3b. Why CURATED needs a GROUP BY on identifiers: some holdings have more than one identifier row
SELECT HOLDING_ID, COUNT(*) AS id_rows
FROM RAW.NPORT_IDENTIFIERS
GROUP BY HOLDING_ID HAVING COUNT(*) > 1
LIMIT 10;

-- 3c. Top 10 holdings of the latest AGG filing
SELECT ISSUER_NAME, ISSUER_TITLE, ASSET_CAT, ROUND(PCT_NET_ASSETS, 3) AS pct, VALUE_USD, COUPON_RATE, MATURITY_DATE
FROM CURATED.FUND_HOLDING
WHERE ACCESSION_NUMBER = $acc
ORDER BY PCT_NET_ASSETS DESC
LIMIT 10;

-- 3d. Do percentages add up? (sanity check, expect ~100)
SELECT ROUND(SUM(PCT_NET_ASSETS), 2) AS total_pct, COUNT(*) AS positions
FROM CURATED.FUND_HOLDING WHERE ACCESSION_NUMBER = $acc;

-- 3e. Mix by asset class and issuer type
SELECT ASSET_CAT, ISSUER_TYPE, COUNT(*) AS positions, ROUND(SUM(PCT_NET_ASSETS), 2) AS pct
FROM CURATED.FUND_HOLDING WHERE ACCESSION_NUMBER = $acc
GROUP BY 1, 2 ORDER BY pct DESC;

-- 3f. Maturity ladder (bond funds)
SELECT WIDTH_BUCKET(DATEDIFF(year, REPORT_DATE, MATURITY_DATE), 0, 30, 6) AS bucket_5y,
       COUNT(*) AS positions, ROUND(SUM(PCT_NET_ASSETS), 2) AS pct
FROM CURATED.FUND_HOLDING WHERE ACCESSION_NUMBER = $acc AND MATURITY_DATE IS NOT NULL
GROUP BY 1 ORDER BY 1;

-- 3g. Raw date strings vs curated dates (what typing changed)
SELECT s.REPORT_DATE AS raw_text, f.REPORT_DATE AS curated_date
FROM RAW.NPORT_SUBMISSION s JOIN CURATED.FUND_FILING f USING (ACCESSION_NUMBER)
WHERE f.SERIES_ID = $series_id;

-- ===== 4. Whole-universe views =====
-- 4a. All 24 funds, tickers, latest as-of date and net assets
SELECT d.TICKERS, d.SERIES_NAME, f.REPORT_DATE, f.FILING_DATE, f.NET_ASSETS
FROM CURATED.DIM_FUND d
JOIN CURATED.FUND_FILING f USING (SERIES_ID)
QUALIFY f.REPORT_DATE = MAX(f.REPORT_DATE) OVER (PARTITION BY d.SERIES_ID)
ORDER BY f.NET_ASSETS DESC;

-- 4b. Row counts of every table and view
SELECT 'RAW.NPORT_FUND_REPORTED_HOLDING' AS object, COUNT(*) AS n FROM RAW.NPORT_FUND_REPORTED_HOLDING
UNION ALL SELECT 'RAW.NPORT_IDENTIFIERS',            COUNT(*) FROM RAW.NPORT_IDENTIFIERS
UNION ALL SELECT 'RAW.NPORT_DEBT_SECURITY',          COUNT(*) FROM RAW.NPORT_DEBT_SECURITY
UNION ALL SELECT 'CURATED.FUND_HOLDING',             COUNT(*) FROM CURATED.FUND_HOLDING
UNION ALL SELECT 'CURATED.FUND_FILING',              COUNT(*) FROM CURATED.FUND_FILING
UNION ALL SELECT 'CURATED.FUND_RETURN_MONTHLY',      COUNT(*) FROM CURATED.FUND_RETURN_MONTHLY;

-- 4c. Which funds hold the same security? (a first look at overlap)
SELECT a.ISSUER_NAME, COUNT(DISTINCT a.SERIES_ID) AS funds_holding_it
FROM CURATED.FUND_HOLDING a
WHERE a.ASSET_CAT = 'EC'
GROUP BY 1 ORDER BY funds_holding_it DESC LIMIT 15;
