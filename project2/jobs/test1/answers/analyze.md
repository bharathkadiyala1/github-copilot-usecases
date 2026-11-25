I'll verify the expected values for the boundary cases before listing them.

Line numbers refer to `test_script.sql` as pasted (90 lines, blank lines included). The SQL dialect isn't stated, so it is UNKNOWN. The syntax is PostgreSQL-compatible, and where behaviour is engine-specific I say so. I checked the tie, cap, margin and QC-rate examples by calculation.

## 1. Business rules

1. **Scope.** One query prices every row in `order_line` with no date, status or order filter, and returns one result row per order, customer and province. Nothing is stored.
2. **Joins.** Each line is inner-joined to its customer, its product and, at the end, to the tax rate of the customer's province (L47–48, L89). A line or order that fails any join is dropped silently.
3. **Gross.** Gross is quantity × the product's current price (L29). The price comes from `product`, so it is not stored per order.
4. **Volume discount (per line).** Quantity ≥ 50 gives 10%, ≥ 20 gives 5%, ≥ 10 gives 2%, otherwise 0 (L32–35). This looks at the line's quantity only, not the order total or the SKU total.
5. **Tier discount.** GOLD gives 5%, SILVER gives 2%, and anything else gives 0 (L38–40). Anything else includes BASIC and NULL.
6. **Promo discount.** `SAVE10` gives 10% and `SAVE5` gives 5%. Any other code, including NULL, gives 0 with no error (L43–45). Matching is exact and case-sensitive. Nothing checks the promo's validity, expiry or eligibility.
7. **Stacking and cap.** The three percentages are added and capped at 15% (L53). The maximum raw sum is 25%, so a stack that reaches the cap loses the excess.
8. **Line rounding.** Net = `ROUND(gross × (1 − discount), 2)` (L59). The comment says ties round away from zero. That is true for PostgreSQL `numeric`. For other engines it is UNKNOWN.
9. **Line cost.** Line cost is quantity × cost, and it is not rounded (L60).
10. **Order totals.** Subtotal is the sum of net, and total cost is the sum of line cost, grouped by `order_id`, `customer_id` and `province` (L66–69). If one order has lines for different customers, it comes out as several rows with the same `order_id`.
11. **Shipping.** Shipping is 0 when subtotal ≥ 100, otherwise a flat 9.99 (L76). The comparison uses the discounted subtotal, and exactly 100.00 ships free.
12. **Tax.** Tax = `ROUND((subtotal + shipping) × province rate, 2)` (L79–80). It is charged on goods plus shipping, at one rate per province, with no exemptions and no GST/PST split.
13. **Margin %.** `ROUND((subtotal − total cost) / subtotal × 100, 1)` (L83). It uses goods only, excluding shipping and tax. A subtotal of 0 gives NULL through `NULLIF`.
14. **Review flag.** `REVIEW` when (subtotal − cost) / subtotal < 0.10, strictly below, otherwise `OK` (L86–87). The test uses the unrounded ratio, not the displayed `margin_pct`.
15. **Ordering.** The result is sorted by `order_id` only (L90). Rows with the same `order_id` come out in no defined order.
16. **Rates data.** The tax rates are seeded in the script (L22): ON 0.13, BC 0.12, AB 0.05, QC 0.14975. The column is `numeric(5,4)` (L21), which has only 4 decimals, so QC is stored as **0.1498** on engines that round on insert (PostgreSQL does). On engines that reject over-scale values, the insert fails instead (UNKNOWN). Whether the rates are correct or current is UNKNOWN.
17. **Rejects.** There is no reject output, log or row reconciliation. Every dropped row is silent.

## 2. Inputs and outputs

| Direction | Object | Columns and types |
|---|---|---|
| Input | `customer` (L2–6) | `customer_id` int PK; `tier` text (CHECK in BASIC, SILVER, GOLD; NULL allowed); `province` char(2) NOT NULL. |
| Input | `product` (L7–11) | `sku` text PK; `price` numeric(10,2) NOT NULL; `cost` numeric(10,2) NOT NULL. No sign check, so negative or zero values are allowed. |
| Input | `order_line` (L12–20) | `order_id` int; `line_no` int (together the PK); `customer_id` int FK, NULL allowed; `sku` text FK, NULL allowed; `qty` int, CHECK > 0 (NULL passes); `promo_code` text, NULL allowed. |
| Input | `tax_rate` (L21–22) | `province` char(2) PK; `rate` numeric(5,4). Seeded in the script with 4 provinces. |
| Input | Data loading | How `customer`, `product` and `order_line` get their data is UNKNOWN. |
| Input | Parameters | None. |
| Output | Result set (L71–90), not saved or exported | See the columns below. |
| Output | `order_id` | int. |
| Output | `subtotal` | numeric, 2 decimals (a sum of rounded values). NULL if every line's quantity is NULL. |
| Output | `shipping` | numeric: `0` or `9.99`. The two branches have different scales, so display may vary by engine (UNKNOWN). |
| Output | `tax` | numeric, 2 decimals. |
| Output | `margin_pct` | numeric, 1 decimal. NULL when the subtotal is 0 or NULL. |
| Output | `margin_flag` | text: `REVIEW` or `OK`. |
| Output | Currency | UNKNOWN. Nothing in the source names it. |

## 3. Technical debt register

| Issue | Where | Risk | Suggested fix |
|---|---|---|---|
| Province with no `tax_rate` row drops the whole order | L89 | Orders vanish from the output and revenue is under-reported. For example, a customer in MB disappears. | Add an FK from `customer.province` to `tax_rate`. Use a LEFT JOIN with an exception report. |
| QC rate stored as 0.1498, not 0.14975 | L21–22 | Tax is overstated. On 1000.00 it is 149.80 instead of 149.75. | Widen to `numeric(7,5)` or more. Confirm the correct rate. |
| Inner joins on nullable `customer_id` and `sku` | L15–16, L47–48 | A line with a NULL customer or SKU disappears with no trace. | Make the columns NOT NULL. Report the lines that fail the joins. |
| Nullable `qty`, and CHECK passes NULL | L17 | A NULL quantity gives a NULL net. It is ignored in the sum, but the order still appears and can show NULL subtotal and tax. | Add NOT NULL to `qty`. |
| Same `order_id` split across customers or provinces | L65–69 | Duplicate `order_id` rows and a nondeterministic order. | Group by `order_id` only. Enforce one customer per order with an order header table. |
| Unstable sort | L90 | Different row order on each run for ties. | Sort by `order_id` plus the grouping columns. |
| Review flag tests the unrounded ratio, but `margin_pct` is rounded | L83, L86 | A row can show `margin_pct` 10.0 and flag `REVIEW`. | Decide which one counts and make both use it. |
| Hardcoded rates, thresholds and codes | L32–45, L53, L76, L22 | Every change needs a code release, with no history or effective dates. | Move them to reference tables (discounts, promos, fees, tax) with effective dates. |
| Promo codes are not validated | L43–45 | A typo gives no discount and no warning. Case and spaces change the result. | Keep a promo table with expiry and eligibility. Report unknown codes. |
| No order header or status | whole file | Cancelled, returned or future orders are all priced. UNKNOWN whether that is intended. | Add order status and date, and filter on them. |
| Price taken from the current `product` row | L28–29 | Re-running later changes past totals. | Store the unit price on the order line. |
| No CHECKs on price, cost or `tier` NULLs | L4, L9–10 | Negative or zero prices give odd margins. | Add CHECK constraints and NOT NULL where appropriate. |
| Rounding behaviour depends on the engine | L59, L79, L83 | Different cents if the job moves to another database. | Document the mode and test it on the target engine. |
| Mixed `0` and `9.99` types in one column | L76 | Display scale varies. | Use `0.00` and `9.99`. |
| Script mixes DDL, seed data and logic; not rerunnable | L1–22 | A second run fails with "table already exists" or duplicate keys. | Separate schema, seed and query. Use migrations. |
| No indexes on `order_line.customer_id` and `sku` | L15–16 | Slow joins at volume. | Add indexes on the join columns. |
| `SELECT *` in the intermediate steps | L51, L57 | Hides columns and is brittle to schema change. | List the columns. |
| No tests, reconciliation or logging | n/a | A rewrite cannot be proven equal. | Build golden tests from section 4, and add row-count checks. |

## 4. Edge cases to test

| # | Setup | What it exposes | Expected (legacy) |
|---|---|---|---|
| 1 | Price 0.25, qty 1, BASIC, `SAVE10` | Rounding tie at the line level (0.225) | Net **0.23** (half-even would give 0.22). |
| 2 | Qty 9, 10, 19, 20, 49, 50 on one SKU | Volume boundaries | Discounts 0, 2%, 2%, 5%, 5%, 10%. |
| 3 | Price 10.00, qty 50, GOLD, `SAVE10` | Cap (raw 25%) | Net **425.00** (15% applied). |
| 4 | Price 10.00, qty 20, GOLD, `SAVE5` | Exactly at the cap (5+5+5) | Net **170.00**, no cap loss. |
| 5 | Price 10.00, qty 10, BASIC | Just under the free-shipping threshold | Subtotal 98.00, shipping 9.99. |
| 6 | Price 100.00, qty 1, BASIC | Free-shipping boundary | Subtotal 100.00, shipping 0. |
| 7 | Subtotal 0.51 in AB (price 0.51, qty 1) | Tax rounding tie ((0.51+9.99)×0.05 = 0.525) | Tax **0.53** (half-even would give 0.52). |
| 8 | Subtotal 1000.00 in QC | Rate stored as 0.1498 | Tax **149.80** (149.75 with the intended rate). |
| 9 | Subtotal 100.00, cost 90.04 | Flag versus rounded margin | `margin_pct` **10.0**, flag **REVIEW**. |
| 10 | Subtotal 100.00, cost 90.00 | Exactly 10% | Flag `OK` (the test is strictly below). |
| 11 | Customer province `MB` (not in `tax_rate`) | Order silently dropped | No row. |
| 12 | Customer province `on` (lower case) | Case-sensitive join | No row, since `ON` does not match `on`. |
| 13 | Customer tier NULL, promo NULL | NULL handling in the CASEs | Both discounts 0. |
| 14 | Promo `save10`, `SAVE10 ` (trailing space) | Exact matching | No discount in either case. |
| 15 | Line with NULL `qty` only | NULL propagation | Subtotal NULL, shipping 9.99, tax NULL, flag `OK`. |
| 16 | Line with NULL `customer_id` or `sku` | Inner-join drop | Line vanishes. |
| 17 | Product price 0.00 | Division by zero guard | `margin_pct` NULL, flag `OK`. |
| 18 | Negative cost or price | No sign checks | Accepted, odd margins. |
| 19 | One order with lines for two customers | Group split | Two rows with the same `order_id`, unstable order. |
| 20 | Duplicate lines (same SKU, different `line_no`) | No dedup | Both priced and added. |
| 21 | Empty `order_line` | Empty input | Zero rows returned. |
| 22 | Re-run of the script | Not rerunnable | Fails on `CREATE TABLE` or the seed `INSERT`. |

Open questions (UNKNOWN from the source): the SQL dialect, the currency, how the tables are loaded, whether order status or date should be filtered, whether the tax rates are correct (especially QC), the intended rounding mode on the target engine, and whether the review flag should follow the displayed margin.