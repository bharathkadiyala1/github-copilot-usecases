-- legacy_etl.sql
-- Daily account summary, written as a chain of "temporary" tables (old PL/SQL habit).
-- Table `raw` is loaded by run_sql_legacy.py (all columns are text).
-- Money is kept as whole cents. The FX rate 1.35 is hardcoded as 135/100 on purpose.

CREATE TABLE s1 AS
SELECT line_no,
       TRIM(txn_id) AS txn_id, TRIM(account_id) AS account_id, TRIM(txn_date) AS txn_date,
       TRIM(txn_type) AS txn_type, TRIM(amount) AS amount, TRIM(currency) AS currency,
       CASE WHEN TRIM(channel) = '' THEN 'UNKNOWN' ELSE UPPER(TRIM(channel)) END AS channel
FROM raw;

CREATE TABLE s2 AS
SELECT s1.*,
       COALESCE(txn_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                AND date(txn_date) = txn_date, 0) AS date_ok,
       CASE WHEN amount GLOB '[0-9]*' AND amount NOT GLOB '*[^0-9.]*' AND amount NOT GLOB '*.*.*'
                 AND (instr(amount, '.') = 0 OR length(amount) - instr(amount, '.') BETWEEN 1 AND 2)
            THEN 1 ELSE 0 END AS amt_ok
FROM s1;

CREATE TABLE s3 AS
SELECT s2.*,
       CASE WHEN amt_ok = 0 THEN NULL
            WHEN instr(amount, '.') = 0 THEN CAST(amount AS INTEGER) * 100
            ELSE CAST(substr(amount, 1, instr(amount, '.') - 1) AS INTEGER) * 100
                 + CAST(substr(amount || '0', instr(amount, '.') + 1, 2) AS INTEGER) END AS cents
FROM s2;

CREATE TABLE s4 AS
SELECT s3.*,
       CASE WHEN txn_id = '' THEN 'MISSING_ID'
            WHEN account_id = '' THEN 'BAD_ACCOUNT'
            WHEN date_ok = 0 THEN 'BAD_DATE'
            WHEN txn_type NOT IN ('DEP','INT','XFRIN','WDR','WIRE','POS','XFROUT') THEN 'BAD_TYPE'
            WHEN amt_ok = 0 OR cents <= 0 THEN 'BAD_AMOUNT'
            WHEN currency NOT IN ('CAD','USD') THEN 'BAD_CURRENCY'
       END AS reason
FROM s3;

-- duplicates: only good rows count, the first one wins
CREATE TABLE s5 AS
SELECT s4.*, ROW_NUMBER() OVER (PARTITION BY txn_id ORDER BY line_no) AS rn
FROM s4 WHERE reason IS NULL;

CREATE TABLE s6 AS
SELECT account_id, txn_date, channel, txn_type,
       CASE WHEN currency = 'USD' THEN (cents * 135 + 50) / 100 ELSE cents END AS cad
FROM s5 WHERE rn = 1;

CREATE TABLE s7 AS
SELECT s6.*,
       CASE WHEN txn_type IN ('DEP','INT','XFRIN') THEN 1 ELSE 0 END AS credit,
       CASE WHEN txn_type = 'WDR' AND channel = 'ATM' AND cad > 50000 THEN 200
            WHEN txn_type = 'WIRE' THEN MIN(MAX((cad + 500) / 1000, 500), 2500)
            ELSE 0 END AS fee
FROM s6;

CREATE TABLE s8 AS
SELECT account_id, COUNT(*) AS n,
       SUM(CASE WHEN credit = 1 THEN cad ELSE 0 END) AS cr,
       SUM(CASE WHEN credit = 0 THEN cad ELSE 0 END) AS dr,
       SUM(fee) AS fees,
       MAX(txn_date) AS last_date,
       SUM(CASE WHEN strftime('%d', date(txn_date, '+1 day')) = '01' THEN 1 ELSE 0 END) AS me,
       SUM(CASE WHEN channel = 'ATM' THEN 1 ELSE 0 END) AS atm
FROM s7 GROUP BY account_id;

CREATE TABLE summary_out AS
SELECT account_id, n AS txn_count,
       printf('%d.%02d', cr / 100, cr % 100) AS total_credits_cad,
       printf('%d.%02d', dr / 100, dr % 100) AS total_debits_cad,
       printf('%d.%02d', fees / 100, fees % 100) AS total_fees_cad,
       CASE WHEN cr - dr - fees < 0
            THEN '-' || printf('%d.%02d', (dr + fees - cr) / 100, (dr + fees - cr) % 100)
            ELSE printf('%d.%02d', (cr - dr - fees) / 100, (cr - dr - fees) % 100) END AS net_cad,
       last_date AS last_txn_date, me AS month_end_txn_count, atm AS atm_txn_count
FROM s8;

CREATE TABLE rejects_out AS
SELECT line_no, txn_id, reason FROM (
    SELECT line_no, txn_id, reason FROM s4 WHERE reason IS NOT NULL
    UNION ALL
    SELECT line_no, txn_id, 'DUPLICATE' FROM s5 WHERE rn > 1
) ORDER BY line_no;
