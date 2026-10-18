USE ROLE FUND_ENGINEER;
USE DATABASE FUND_AI;
USE SCHEMA CURATED;

-- N-PORT code lists, so humans (and the LLM) see words instead of codes
CREATE OR REPLACE VIEW DIM_ASSET_CATEGORY AS
SELECT * FROM VALUES
  ('EC','Common equity'),('EP','Preferred equity'),('DBT','Debt (bonds, notes)'),
  ('ABS-MBS','Mortgage-backed security'),('ABS-CBDO','CDO / CLO'),('ABS-APCP','Asset-backed commercial paper'),
  ('ABS-O','Other asset-backed security'),('LON','Loan'),('SN','Structured note'),
  ('STIV','Short-term investment vehicle'),('RA','Repurchase agreement'),
  ('DE','Derivative - equity'),('DIR','Derivative - interest rate'),('DCR','Derivative - credit'),
  ('DFE','Derivative - foreign exchange'),('DCO','Derivative - commodity'),('DO','Derivative - other'),
  ('COMM','Commodity'),('RE','Real estate'),('OTHER','Other')
AS t(ASSET_CAT, ASSET_CLASS);

CREATE OR REPLACE VIEW DIM_ISSUER_TYPE AS
SELECT * FROM VALUES
  ('CORP','Corporate'),('UST','US Treasury'),('USGA','US government agency'),
  ('USGSE','US government-sponsored enterprise'),('MUN','Municipal'),('NUSS','Non-US sovereign'),
  ('PF','Private fund'),('RF','Registered fund'),('OTHER','Other')
AS t(ISSUER_TYPE, ISSUER_CATEGORY);

-- one row per security per filing (a security can appear on several lines, e.g. different lots)
CREATE OR REPLACE VIEW FUND_POSITION AS
SELECT SERIES_ID, ACCESSION_NUMBER, REPORT_DATE, SECURITY_KEY,
       ANY_VALUE(ISSUER_NAME)   AS ISSUER_NAME,
       ANY_VALUE(ISSUER_TITLE)  AS ISSUER_TITLE,
       ANY_VALUE(CUSIP)         AS CUSIP,
       ANY_VALUE(ISIN)          AS ISIN,
       ANY_VALUE(ASSET_CAT)     AS ASSET_CAT,
       ANY_VALUE(ISSUER_TYPE)   AS ISSUER_TYPE,
       ANY_VALUE(COUNTRY)       AS COUNTRY,
       SUM(BALANCE)             AS BALANCE,          -- shares or principal, same unit for a given key
       SUM(VALUE_USD)           AS VALUE_USD,
       SUM(PCT_NET_ASSETS)      AS PCT_NET_ASSETS
FROM FUND_HOLDING
GROUP BY SERIES_ID, ACCESSION_NUMBER, REPORT_DATE, SECURITY_KEY;
