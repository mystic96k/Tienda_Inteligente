-- Warehouse-scale ML in BigQuery (BQML). Data arrives via Datastream (Cloud SQL -> BigQuery) or nightly export.
-- 1) Time-series demand model per (branch, product). ARIMA_PLUS handles seasonality, holidays, spikes.
CREATE OR REPLACE MODEL `retail_dw.demand_arima`
OPTIONS (model_type = 'ARIMA_PLUS', time_series_timestamp_col = 'sale_date',
         time_series_data_col = 'units', time_series_id_col = ['branch_id', 'product_id'],
         holiday_region = 'MX', auto_arima_max_order = 4) AS
SELECT sale_date, branch_id, product_id, units FROM `retail_dw.sales_daily`;

-- 2) 28-day forecast with 80% interval, written back for Power BI / the API
CREATE OR REPLACE TABLE `retail_dw.forecast_28d` AS
SELECT * FROM ML.FORECAST(MODEL `retail_dw.demand_arima`, STRUCT(28 AS horizon, 0.8 AS confidence_level));

-- 3) Promo uplift model with price/discount as a feature (gradient boosted trees)
CREATE OR REPLACE MODEL `retail_dw.promo_uplift`
OPTIONS (model_type = 'BOOSTED_TREE_REGRESSOR', input_label_cols = ['units'], max_iterations = 200) AS
SELECT units, branch_id, product_id, EXTRACT(DAYOFWEEK FROM sale_date) AS dow, EXTRACT(MONTH FROM sale_date) AS month,
       discount_pct, unit_price
FROM `retail_dw.sales_daily` JOIN `retail_dw.products` USING (product_id);

-- 4) Anomaly detection on daily revenue (data-quality / theft / outage signal)
SELECT * FROM ML.DETECT_ANOMALIES(MODEL `retail_dw.demand_arima`, STRUCT(0.97 AS anomaly_prob_threshold));
