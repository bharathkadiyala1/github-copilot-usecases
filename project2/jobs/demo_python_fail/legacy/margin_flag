-- ===== Reference data =====
CREATE TABLE customer (
    customer_id  int PRIMARY KEY,
    tier         text CHECK (tier IN ('BASIC','SILVER','GOLD')),
    province     char(2) NOT NULL
);
CREATE TABLE product (
    sku       text PRIMARY KEY,
    price     numeric(10,2) NOT NULL,
    cost      numeric(10,2) NOT NULL
);
CREATE TABLE order_line (
    order_id     int,
    line_no      int,
    customer_id  int REFERENCES customer,
    sku          text REFERENCES product,
    qty          int CHECK (qty > 0),
    promo_code   text,
    PRIMARY KEY (order_id, line_no)
);
CREATE TABLE tax_rate (province char(2) PRIMARY KEY, rate numeric(5,4));
INSERT INTO tax_rate VALUES ('ON',0.13),('BC',0.12),('AB',0.05),('QC',0.14975);

-- ===== Business logic =====
WITH line_calc AS (
    SELECT
        l.order_id, l.line_no, c.customer_id, c.province,
        p.price, p.cost, l.qty,
        l.qty * p.price AS gross,

        -- Rule 1: volume discount by quantity per line.
        CASE WHEN l.qty >= 50 THEN 0.10
             WHEN l.qty >= 20 THEN 0.05
             WHEN l.qty >= 10 THEN 0.02
             ELSE 0 END AS vol_pct,

        -- Rule 2: loyalty tier discount.
        CASE c.tier WHEN 'GOLD' THEN 0.05
                    WHEN 'SILVER' THEN 0.02
                    ELSE 0 END AS tier_pct,

        -- Rule 3: promo codes (unknown code = no discount, not an error).
        CASE l.promo_code WHEN 'SAVE10' THEN 0.10
                          WHEN 'SAVE5'  THEN 0.05
                          ELSE 0 END AS promo_pct
    FROM order_line l
    JOIN customer c USING (customer_id)
    JOIN product  p USING (sku)
),
discounted AS (
    SELECT *,
        -- Rule 4: discounts stack but are capped at 15% total.
        LEAST(vol_pct + tier_pct + promo_pct, 0.15) AS disc_pct
    FROM line_calc
),
priced AS (
    SELECT *,
        -- Rule 5: round each line to the cent, ties away from zero.
        ROUND(gross * (1 - disc_pct), 2)  AS net,
        qty * cost                        AS line_cost
    FROM discounted
),
order_tot AS (
    SELECT
        order_id, customer_id, province,
        SUM(net)       AS subtotal,
        SUM(line_cost) AS total_cost
    FROM priced
    GROUP BY order_id, customer_id, province
)
SELECT
    o.order_id,
    o.subtotal,

    -- Rule 6: free shipping at $100 or more, flat $9.99 below that.
    CASE WHEN o.subtotal >= 100 THEN 0 ELSE 9.99 END AS shipping,

    -- Rule 7: tax applies to goods plus shipping, at the customer's province rate.
    ROUND((o.subtotal + CASE WHEN o.subtotal >= 100 THEN 0 ELSE 9.99 END)
          * t.rate, 2) AS tax,

    -- Rule 8: margin on goods only.
    ROUND((o.subtotal - o.total_cost) / NULLIF(o.subtotal,0) * 100, 1) AS margin_pct,

    -- Rule 9: flag low-margin orders for review.
    CASE WHEN (o.subtotal - o.total_cost) / NULLIF(o.subtotal,0) < 0.10
         THEN 'REVIEW' ELSE 'OK' END AS margin_flag
FROM order_tot o
JOIN tax_rate t USING (province)
ORDER BY o.order_id;