-- Run as FUND_ENGINEER. Landing zone for locally-downloaded SEC files.
USE ROLE FUND_ENGINEER;
USE DATABASE FUND_AI;
USE SCHEMA RAW;

-- SEC N-PORT data sets are tab-separated with a header row.
CREATE FILE FORMAT IF NOT EXISTS TSV_HEADER
  TYPE = CSV
  FIELD_DELIMITER = '\t'
  SKIP_HEADER = 1
  FIELD_OPTIONALLY_ENCLOSED_BY = NONE
  NULL_IF = ('')
  EMPTY_FIELD_AS_NULL = TRUE
  ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE; -- ponytail: tolerant of SEC schema drift; tighten once columns are pinned

CREATE STAGE IF NOT EXISTS SEC_STAGE
  FILE_FORMAT = TSV_HEADER
  DIRECTORY = (ENABLE = TRUE);

-- one row per file loaded: idempotency + audit
CREATE TABLE IF NOT EXISTS LOAD_MANIFEST (
  source      STRING,
  period      STRING,
  file_name   STRING,
  row_count   NUMBER,
  status      STRING,
  loaded_at   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
