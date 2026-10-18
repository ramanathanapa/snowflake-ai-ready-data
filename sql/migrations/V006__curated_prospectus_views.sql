-- Prospectus (rr1) layer: fees, calendar-year returns, objective/strategy text.
-- Source facts are fractions (0.0003 = 0.03%); curated columns are PERCENT, like PCT_NET_ASSETS.
-- Each fund files once a year, so every view keeps the LATEST filed value per series/class.
USE ROLE FUND_ENGINEER;
USE DATABASE FUND_AI;
USE SCHEMA CURATED;

-- numeric facts, typed, with the share class pulled out of OTHERDIMS ('Class=C000012040;')
CREATE OR REPLACE VIEW PROSPECTUS_NUM AS
SELECT n.ADSH,
       n.SERIES                                            AS SERIES_ID,
       REGEXP_SUBSTR(n.OTHERDIMS, 'Class=([^;]+)', 1, 1, 'e', 1) AS CLASS_ID,  -- NULL for fund-level facts (turnover)
       n.TAG,
       TO_DATE(n.DDATE, 'YYYYMMDD')                        AS FACT_DATE,
       TRY_TO_DOUBLE(n.VALUE)                              AS VALUE,
       TO_DATE(s.FILED, 'YYYYMMDD')                        AS FILED_DATE,
       s.FORM
FROM RAW.RR_NUM n
JOIN RAW.RR_SUB s USING (ADSH);

-- one row per share class: latest fee table
CREATE OR REPLACE VIEW FUND_FEES AS
WITH latest AS (
  SELECT * FROM PROSPECTUS_NUM
  WHERE CLASS_ID IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY SERIES_ID, CLASS_ID, TAG ORDER BY FILED_DATE DESC, FACT_DATE DESC, ADSH DESC) = 1
)
SELECT l.SERIES_ID, l.CLASS_ID, c.TICKER,
       MAX(IFF(TAG = 'ExpensesOverAssets',                  VALUE, NULL)) * 100 AS EXPENSE_RATIO_PCT,
       MAX(IFF(TAG = 'NetExpensesOverAssets',               VALUE, NULL)) * 100 AS NET_EXPENSE_RATIO_PCT,  -- after waivers; often NULL
       MAX(IFF(TAG = 'ManagementFeesOverAssets',            VALUE, NULL)) * 100 AS MANAGEMENT_FEE_PCT,
       MAX(IFF(TAG = 'DistributionAndService12b1FeesOverAssets', VALUE, NULL)) * 100 AS FEE_12B1_PCT,
       MAX(IFF(TAG = 'OtherExpensesOverAssets',             VALUE, NULL)) * 100 AS OTHER_EXPENSES_PCT,
       MAX(IFF(TAG = 'MaximumSalesChargeImposedOnPurchasesOverOfferingPrice', VALUE, NULL)) * 100 AS MAX_FRONT_LOAD_PCT,
       MAX(IFF(TAG = 'ExpenseExampleYear01',                VALUE, NULL))       AS COST_OF_10K_1Y_USD,
       MAX(IFF(TAG = 'ExpenseExampleYear10',                VALUE, NULL))       AS COST_OF_10K_10Y_USD,
       MAX(FILED_DATE)                                                          AS FILED_DATE,
       MAX(FACT_DATE)                                                           AS FEES_AS_OF
FROM latest l
LEFT JOIN DIM_FUND_CLASS c USING (SERIES_ID, CLASS_ID)
GROUP BY l.SERIES_ID, l.CLASS_ID, c.TICKER;

-- calendar-year total return per share class (the prospectus bar chart), latest filing wins per year
CREATE OR REPLACE VIEW FUND_ANNUAL_RETURN AS
SELECT p.SERIES_ID, p.CLASS_ID, c.TICKER,
       YEAR(p.FACT_DATE)  AS CALENDAR_YEAR,
       p.VALUE * 100      AS TOTAL_RETURN_PCT
FROM PROSPECTUS_NUM p
LEFT JOIN DIM_FUND_CLASS c USING (SERIES_ID, CLASS_ID)
WHERE p.TAG = 'AnnlRtrPct'
QUALIFY ROW_NUMBER() OVER (PARTITION BY p.SERIES_ID, p.CLASS_ID, YEAR(p.FACT_DATE)
                           ORDER BY p.FILED_DATE DESC, p.ADSH DESC) = 1;

-- fund-level (no class): portfolio turnover, latest
CREATE OR REPLACE VIEW FUND_TURNOVER AS
SELECT SERIES_ID, VALUE * 100 AS TURNOVER_PCT, FACT_DATE AS AS_OF, FILED_DATE
FROM PROSPECTUS_NUM
WHERE TAG = 'PortfolioTurnoverRate'
QUALIFY ROW_NUMBER() OVER (PARTITION BY SERIES_ID ORDER BY FILED_DATE DESC, FACT_DATE DESC, ADSH DESC) = 1;

-- narrative text for Cortex Search: objective, strategy, risks, etc. HTML tags and entities stripped.
CREATE OR REPLACE VIEW FUND_PROSPECTUS_TEXT AS
SELECT t.SERIES_ID, d.TICKERS, t.TAG AS SECTION, t.FILED_DATE, t.TEXT
FROM (
  SELECT x.SERIES AS SERIES_ID, x.TAG, TO_DATE(s.FILED, 'YYYYMMDD') AS FILED_DATE, x.ADSH,
         TRIM(REGEXP_REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REGEXP_REPLACE(x.VALUE, '<[^>]+>', ' '),
              '&amp;', '&'), '&nbsp;', ' '), '&lt;', '<'), '&gt;', '>'), '[[:space:]]+', ' ')) AS TEXT
  FROM RAW.RR_TXT x
  JOIN RAW.RR_SUB s USING (ADSH)
  WHERE x.TAG IN ('ObjectivePrimaryTextBlock', 'StrategyNarrativeTextBlock', 'RiskTextBlock',
                  'PortfolioTurnoverTextBlock', 'PerformanceNarrativeTextBlock', 'ExpenseNarrativeTextBlock')
) t
JOIN DIM_FUND d USING (SERIES_ID)
QUALIFY ROW_NUMBER() OVER (PARTITION BY t.SERIES_ID, t.TAG, t.TEXT ORDER BY t.FILED_DATE DESC, t.ADSH DESC) = 1;
