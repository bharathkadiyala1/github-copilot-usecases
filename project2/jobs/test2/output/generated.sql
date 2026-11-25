-- Rule 15: load strategy is given as MERGE; this statement is an INSERT as requested (see open questions)
INSERT INTO DWH.FACT_ACCOUNT_DAILY (
    ACCOUNT_ID, BALANCE_DATE, CUSTOMER_KEY, PRODUCT_KEY, BRANCH_KEY,
    CLOSING_BALANCE_CAD, AVAILABLE_BALANCE_CAD, ORIGINAL_CURRENCY, FX_RATE_APPLIED,
    TOTAL_DEBITS_CAD, TOTAL_CREDITS_CAD, TXN_COUNT, ACCOUNT_STATUS, ACCOUNT_AGE_DAYS,
    OVERDRAWN_FLAG, LOAD_TS, BATCH_ID
)
SELECT
    a.ACCT_ID                                                   AS ACCOUNT_ID,            -- M01
    db.BAL_DATE                                                 AS BALANCE_DATE,          -- M02
    COALESCE(c.CUSTOMER_KEY, -1)                                AS CUSTOMER_KEY,          -- M03
    COALESCE(p.PRODUCT_KEY, -1)                                 AS PRODUCT_KEY,           -- M04
    COALESCE(                                                                             -- M05
        (SELECT br.BRANCH_KEY FROM DWH.DIM_BRANCH br WHERE br.BRANCH_NO = b.BRANCH_NO),
        -1)                                                     AS BRANCH_KEY,
    CASE WHEN db.CURRENCY_CD = 'CAD' THEN db.CLOSING_BAL                                  -- M06
         ELSE db.CLOSING_BAL * COALESCE(
                  fx.RATE,
                  (SELECT MAX_BY(fx2.RATE, fx2.RATE_DATE) FROM REF.FX_RATE fx2
                    WHERE fx2.FROM_CCY = db.CURRENCY_CD AND fx2.TO_CCY = 'CAD'
                      AND fx2.RATE_DATE < db.BAL_DATE))
    END                                                         AS CLOSING_BALANCE_CAD,
    CASE WHEN db.CURRENCY_CD = 'CAD' THEN db.AVAIL_BAL                                    -- M07
         ELSE db.AVAIL_BAL * COALESCE(
                  fx.RATE,
                  (SELECT MAX_BY(fx2.RATE, fx2.RATE_DATE) FROM REF.FX_RATE fx2
                    WHERE fx2.FROM_CCY = db.CURRENCY_CD AND fx2.TO_CCY = 'CAD'
                      AND fx2.RATE_DATE < db.BAL_DATE))
    END                                                         AS AVAILABLE_BALANCE_CAD,
    UPPER(TRIM(db.CURRENCY_CD))                                 AS ORIGINAL_CURRENCY,     -- M08
    CASE WHEN db.CURRENCY_CD = 'CAD' THEN 1.000000                                        -- M09
         ELSE COALESCE(
                  fx.RATE,
                  (SELECT MAX_BY(fx2.RATE, fx2.RATE_DATE) FROM REF.FX_RATE fx2
                    WHERE fx2.FROM_CCY = db.CURRENCY_CD AND fx2.TO_CCY = 'CAD'
                      AND fx2.RATE_DATE < db.BAL_DATE))
    END                                                         AS FX_RATE_APPLIED,
    COALESCE(t.DEBIT_AMT, 0)                                    AS TOTAL_DEBITS_CAD,      -- M10
    COALESCE(t.CREDIT_AMT, 0)                                   AS TOTAL_CREDITS_CAD,     -- M11
    COALESCE(t.TXN_CNT, 0)                                      AS TXN_COUNT,             -- M12
    CASE a.STATUS_CD WHEN 'A' THEN 'ACTIVE'                                               -- M13
                     WHEN 'D' THEN 'DORMANT'
                     WHEN 'C' THEN 'CLOSED'
                     ELSE 'UNKNOWN' END                         AS ACCOUNT_STATUS,
    DATEDIFF(day, a.OPEN_DT, db.BAL_DATE)                       AS ACCOUNT_AGE_DAYS,      -- M14
    CASE WHEN db.CLOSING_BAL < 0 THEN 'Y' ELSE 'N' END          AS OVERDRAWN_FLAG,        -- M15
    CURRENT_TIMESTAMP()                                         AS LOAD_TS,               -- M16
    :batch_id                                                   AS BATCH_ID               -- M17
FROM CBS.DAILY_BALANCE db                                                                 -- Rule 1
INNER JOIN CBS.ACCOUNT a                                                                  -- Rule 2
        ON db.ACCT_ID = a.ACCT_ID
LEFT JOIN CBS.BRANCH b                                                                    -- Rule 3
       ON a.BRANCH_NO = b.BRANCH_NO
LEFT JOIN REF.FX_RATE fx                                                                  -- Rule 4
       ON db.CURRENCY_CD = fx.FROM_CCY
      AND fx.TO_CCY = 'CAD'
      AND db.BAL_DATE = fx.RATE_DATE
LEFT JOIN (                                                                               -- Rule 5 (t)
    SELECT x.ACCT_ID, x.TXN_DT,
           SUM(CASE WHEN x.TXN_TYPE = 'D' THEN x.amt END) AS DEBIT_AMT,                   -- feeds M10
           SUM(CASE WHEN x.TXN_TYPE = 'C' THEN x.amt END) AS CREDIT_AMT,                  -- feeds M11
           SUM(x.cnt)                                     AS TXN_CNT                      -- feeds M12
    FROM (
        SELECT ACCT_ID, TXN_DT, TXN_TYPE, SUM(TXN_AMT) amt, COUNT(DISTINCT TXN_ID) cnt
        FROM CBS.TXN
        WHERE TXN_STATUS = 'POSTED'                                                       -- Rule 10 (filter on t)
        GROUP BY ACCT_ID, TXN_DT, TXN_TYPE
    ) x
    GROUP BY x.ACCT_ID, x.TXN_DT                                                          -- Rule 14 (sub-query level)
) t
       ON db.ACCT_ID = t.ACCT_ID
      AND db.BAL_DATE = t.TXN_DT
LEFT JOIN DWH.DIM_CUSTOMER c                                                              -- Rule 6
       ON a.CUST_ID = c.CUST_ID
      AND c.CURRENT_FLAG = 'Y'
LEFT JOIN DWH.DIM_PRODUCT p                                                               -- Rule 7
       ON a.PROD_CD = p.PROD_CD
WHERE db.BAL_DATE >= :run_date - 3                                                        -- Rule 8
  AND db.BAL_DATE <= :run_date                                                            -- Rule 8
  AND a.ACCT_TYPE <> 'TEST'                                                               -- Rule 9
  AND a.ACCT_ID NOT IN (SELECT ACCT_ID FROM CBS.TEST_ACCOUNTS)                            -- Rule 9
  -- Rule 10 is applied inside the t sub-query (TXN_STATUS = 'POSTED')
  AND db.CURRENCY_CD IN (SELECT CCY_CD FROM REF.CURRENCY WHERE ACTIVE_FLAG = 'Y')         -- Rule 11
  AND db.CLOSING_BAL IS NOT NULL                                                          -- Rule 12
QUALIFY ROW_NUMBER() OVER (PARTITION BY db.ACCT_ID, db.BAL_DATE                           -- Rule 13
                           ORDER BY db.LAST_UPD_TS DESC) = 1;
