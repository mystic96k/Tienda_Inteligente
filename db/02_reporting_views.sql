-- Reporting layer for Power BI / any BI tool. Applied automatically by the API on PostgreSQL start-up.
-- Connect BI with a READ-ONLY role limited to schema rpt (see powerbi/README.md).
CREATE SCHEMA IF NOT EXISTS rpt;

CREATE OR REPLACE VIEW rpt.vw_sales_fact AS
SELECT s.date, st.store_id, st.store_name, st.format AS store_format, si.site_name, si.site_type, si.region,
       p.department, p.category, p.sku, p.product_name,
       s.units_sold, s.gross_sales, s.discount_amount, s.net_sales,
       s.units_sold * p.unit_cost AS cogs, s.net_sales - s.units_sold * p.unit_cost AS gross_profit,
       s.promo_id, CASE WHEN s.promo_id IS NULL THEN 0 ELSE 1 END AS on_promo, s.dq_flag,
       c.is_holiday, c.is_weekend, c.is_school_break
FROM sales_daily s
JOIN stores st ON st.store_id = s.store_id
JOIN sites si ON si.site_id = st.site_id
JOIN products p ON p.sku = s.sku
LEFT JOIN calendar c ON c.date = s.date;

CREATE OR REPLACE VIEW rpt.vw_inventory_fact AS
SELECT i.date, st.store_id, st.store_name, si.region, p.sku, p.product_name, p.category, p.shelf_life_days,
       i.opening_qty, i.received_qty, i.units_sold, i.expired_qty, i.damaged_qty, i.closing_qty, i.on_order_qty, i.stockout_hours,
       i.expired_qty * p.unit_cost AS waste_cost, i.closing_qty * p.unit_cost AS stock_value, i.dq_flag
FROM inventory_daily i
JOIN stores st ON st.store_id = i.store_id
JOIN sites si ON si.site_id = st.site_id
JOIN products p ON p.sku = i.sku;

CREATE OR REPLACE VIEW rpt.vw_store_kpis AS
SELECT k.date, st.store_id, st.store_name, st.format AS store_format, si.site_name, si.site_type, si.region, si.traffic_tier,
       k.footfall, k.transactions, k.units_sold, k.net_sales, k.staff_hours, k.open_hours,
       CASE WHEN k.staff_hours > 0 THEN k.net_sales / k.staff_hours END AS sales_per_labour_hour,
       w.temp_max_c, w.precip_mm
FROM store_daily_kpis k
JOIN stores st ON st.store_id = k.store_id
JOIN sites si ON si.site_id = st.site_id
LEFT JOIN weather_daily w ON w.site_id = st.site_id AND w.date = k.date;

CREATE OR REPLACE VIEW rpt.vw_store_forecast AS
SELECT f.forecast_date, st.store_id, st.store_name, f.net_sales AS net_sales_forecast, f.lower, f.upper, f.model_version
FROM store_forecast f JOIN stores st ON st.store_id = f.store_id;

CREATE OR REPLACE VIEW rpt.vw_demand_forecast AS
SELECT f.forecast_date, st.store_id, st.store_name, p.sku, p.product_name, p.category, f.units AS units_forecast, f.lower, f.upper
FROM demand_forecast f JOIN stores st ON st.store_id = f.store_id JOIN products p ON p.sku = f.sku;

CREATE OR REPLACE VIEW rpt.vw_staffing_plan AS
SELECT sp.plan_date, st.store_id, st.store_name, sp.forecast_net_sales, sp.target_splh, sp.required_hours,
       sp.baseline_hours, sp.delta_hours, sp.status
FROM staffing_plan sp JOIN stores st ON st.store_id = sp.store_id;

CREATE OR REPLACE VIEW rpt.vw_recommendations AS
SELECT r.id, st.store_id, st.store_name, r.kind, r.priority, r.title, r.summary, r.est_impact, r.status, r.created_at, r.decided_at
FROM recommendations r JOIN stores st ON st.store_id = r.store_id;

CREATE OR REPLACE VIEW rpt.vw_promo_performance AS
SELECT p.promo_id, p.promo_name, p.promo_type, p.start_date, p.end_date, p.discount_pct, p.department, p.category, p.region_scope,
       pp.baseline_units_per_store_day, pp.promo_units_per_store_day, pp.realized_uplift_pct, pp.expected_uplift_pct, pp.is_valid, pp.note
FROM promo_performance pp JOIN promotions p ON p.promo_id = pp.promo_id;

CREATE OR REPLACE VIEW rpt.vw_supplier_performance AS
SELECT su.supplier_id, su.supplier_name, su.category_focus, su.standard_lead_time_days, sp.po_lines, sp.fill_rate, sp.on_time_rate,
       sp.contract_fill_rate, sp.contract_on_time_rate
FROM supplier_performance sp JOIN suppliers su ON su.supplier_id = sp.supplier_id;

CREATE OR REPLACE VIEW rpt.vw_data_quality AS
SELECT table_name, issue, rows_affected, treatment FROM dq_issues;

CREATE OR REPLACE VIEW rpt.vw_daily_plan AS
SELECT p.id, p.store_id, s.store_name, p.plan_date, p.kind, p.sku, pr.product_name, p.title, p.detail, p.qty, p.est_impact, p.priority, p.rec_id
FROM daily_plan p JOIN stores s USING (store_id) LEFT JOIN products pr USING (sku);
