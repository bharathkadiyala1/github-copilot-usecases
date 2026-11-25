## In one paragraph
This script works out the price of customer orders from a list of order lines. It applies discounts, adds shipping, adds sales tax and checks how much profit each order makes. It produces one result row per order with the goods total (subtotal), shipping, tax, profit margin percent and a flag, either `REVIEW` or `OK`. The results are only shown on screen: nothing is saved or sent anywhere. The script also creates its own small tables (a table is a grid of rows and columns, like a spreadsheet tab) and fills in the tax rates.

## Step by step
1. The script creates four tables: customers, products, order lines and tax rates.
2. It fills the tax rate table with four provinces: ON, BC, AB and QC.
3. For each order line, it looks up the customer and the product. A line without a matching customer or product is dropped without a message.
4. It works out the line's gross price: quantity times the product's price.
5. It works out three discount percentages for the line: one for quantity, one for the customer's loyalty tier and one for the promo code.
6. It adds the three percentages together and caps the total at 15%.
7. It reduces the gross price by that percentage and rounds the result to the cent. This is the line's net price.
8. It also works out the line's cost: quantity times the product's cost.
9. It adds up the net prices and costs for each order. This gives the order's subtotal (goods total after discounts) and total cost.
10. It sets shipping to 0 if the subtotal is 100 or more, and to 9.99 otherwise.
11. It looks up the tax rate for the customer's province. Orders whose province is not in the tax table are dropped without a message.
12. It calculates tax on the subtotal plus shipping, rounded to the cent.
13. It calculates the margin percent: (subtotal minus cost) divided by subtotal, times 100, rounded to one decimal. This is profit as a share of sales.
14. It sets the flag to `REVIEW` if the margin is under 10%, otherwise `OK`.
15. It lists the results in order number order.

## Business rules
1. Lines of 50 or more units get a 10% quantity discount.
2. Lines of 20 to 49 units get 5%.
3. Lines of 10 to 19 units get 2%.
4. Lines under 10 units get no quantity discount.
5. Gold customers get a 5% loyalty discount.
6. Silver customers get a 2% loyalty discount.
7. Basic customers, and customers with no tier, get no loyalty discount.
8. The promo code `SAVE10` gives 10% off the line.
9. The promo code `SAVE5` gives 5% off the line.
10. Any other promo code, or none, gives no discount and no error.
11. The quantity, loyalty and promo discounts add together, but the total discount on a line never goes above 15%.
12. Each line is rounded to the cent, with halves rounded up (away from zero).
13. Orders with a subtotal of $100 or more ship free.
14. Orders under $100 pay a flat $9.99 shipping.
15. Tax is charged on goods plus shipping, at the rate for the customer's province: ON 13%, BC 12%, AB 5%, QC 14.975%.
16. Margin is worked out on goods only, so shipping and tax are left out.
17. An order whose margin is below 10% is flagged `REVIEW`. Exactly 10% is `OK`.
18. An order with a subtotal of 0 has no margin and is flagged `OK`.

## Words you will see
| Term | Plain meaning |
|---|---|
| CUSTOMER | The table of customers. |
| PRODUCT | The table of products with their selling price and cost. |
| ORDER_LINE | The table of order lines. Each line is one product on one order. |
| TAX_RATE | The table that holds one tax rate per province. |
| customer_id | The number that identifies a customer. |
| tier | The customer's loyalty level: BASIC, SILVER or GOLD. |
| province | The two-letter province code, such as ON. |
| sku | The product code (stock keeping unit). |
| price | What the customer pays for one unit. |
| cost | What the product costs the business per unit. |
| order_id | The order number. |
| line_no | The line number within an order. |
| qty | The quantity (number of units) on a line. |
| promo_code | A discount code typed in by the customer, such as SAVE10. |
| gross | Quantity times price, before discounts. |
| disc_pct | The total discount percent after the cap. |
| net | The line price after discounts, rounded to the cent. |
| line_cost | Quantity times cost for one line. |
| subtotal | The order's goods total after discounts. |
| total_cost | The order's total cost. |
| shipping | The delivery charge. |
| tax | The sales tax charged. |
| margin_pct | Profit as a percent of the subtotal. |
| margin_flag | `REVIEW` means a low margin that needs a person to look at it. `OK` means no action. |
| NULL | An empty value, meaning "nothing recorded". |
| Join | Matching rows in two tables, for example a line to its customer. |
| CASE | A rule of the form "if this, then that". |

## Things to double check
- **Hardcoded values.** The rates and limits are written into the script: 0.02, 0.05, 0.10, 0.12, 0.13, 0.14975, 0.15 and 9.99. So are the quantity cut-offs (10, 20, 50), the free-shipping limit (100), the promo code names and the 10% review limit. Ask whether these should be kept in tables the business can change.
- **Quebec tax rate.** The tax table column holds only four decimals. On engines that round when saving (PostgreSQL does), QC is stored as 0.1498 instead of 0.14975. On 1000.00 that is tax of 149.80 instead of 149.75. On other engines the insert could fail instead (UNKNOWN). Ask which rate is correct.
- **Missing provinces.** A customer in a province not on the tax list, such as MB, makes their whole order vanish from the results with no message. A lowercase province such as `on` does the same. Ask how other provinces should be handled.
- **Silent drops.** A line with no customer or no product also disappears silently. Ask whether these should be reported.
- **Empty quantity.** A line with no quantity is allowed. Its order can show an empty subtotal and tax.
- **Promo codes.** A typo, lowercase or an extra space gives no discount and no warning. There is no check for expiry or eligibility.
- **Discount cap.** A raw total of 25% (for example 50 units, a Gold customer and `SAVE10`) is silently cut to 15%. Confirm the cap is intended.
- **Margin versus flag.** The flag uses the exact margin, but the percent shown is rounded. An order at 9.96% shows 10.0 and is still flagged `REVIEW`.
- **Split orders.** If one order has lines for different customers, it appears as several rows with the same order number, in no fixed order.
- **Prices are looked up today.** The script uses the product's current price and cost, not what was charged at the time of the order.
- **Negative or zero prices and costs.** Nothing stops them, and they produce odd margins.
- **Rounding rule.** The comment says halves round away from zero. That holds for PostgreSQL. The database type is UNKNOWN, and other engines may round differently.
- **No dates or order status.** Cancelled, returned or future orders are all included. Ask whether that is intended.
- **Currency.** UNKNOWN.
- **Not rerunnable.** Running the script twice fails because the tables already exist.
- **Where the data comes from.** UNKNOWN. How the customer, product and order tables are filled is not in this script.

## Test ideas
- **Quantity cut-offs.** Price 10.00, BASIC customer, no promo: quantity 9 should give 90.00, quantity 10 should give 98.00, quantity 19 should give 186.20, quantity 20 should give 190.00 and quantity 50 should give 450.00.
- **Loyalty tiers.** Gold, Silver, Basic and no tier on the same product, to show 5%, 2%, 0 and 0.
- **Promo codes.** `SAVE10`, `SAVE5`, `UNKNOWN99`, `save10` (lowercase) and `SAVE10 ` (with a trailing space). Only the first two should give a discount.
- **Cap.** Price 10.00, quantity 50, Gold, `SAVE10` should give a net of 425.00. Quantity 20, Gold, `SAVE5` (exactly 15%) should give 170.00.
- **Rounding on the cent.** Price 0.25, quantity 1, BASIC, `SAVE10` (0.225) should give 0.23.
- **Free shipping.** Subtotals of 99.99 (shipping 9.99), 100.00 (shipping 0) and 98.00 (shipping 9.99).
- **Tax.** Subtotal 0.51 in AB, where tax on 10.50 at 5% should be 0.53. Subtotal 1000.00 in QC, which should show 149.80 with the rate as stored.
- **Missing province.** A customer in MB and one in `on`. Neither order should appear.
- **Margin flag.** Subtotal 100.00 with cost 90.00 should be `OK` (margin 10.0). Cost 90.04 should show margin 10.0 but `REVIEW`.
- **Zero price.** A product with price 0.00 should give an empty margin and `OK`.
- **Empty values.** A line with no quantity, one with no customer and one with no product.
- **Two customers on one order.** Two rows should come out with the same order number.
- **Duplicate lines.** Two identical lines with different line numbers should both count.
- **Empty input.** No order lines should give no result rows.