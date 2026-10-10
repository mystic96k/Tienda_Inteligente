-- RetailIQ / Glazed Co. - PostgreSQL 16 schema (Cloud SQL compatible).
-- GENERATED from backend/app/models.py by scripts/export_schema.py - edit the models, not this file.
-- Order: dimensions -> facts -> governance -> analytics -> decisions (tables are topologically sorted by FK).
-- docker compose mounts this folder into postgres:/docker-entrypoint-initdb.d, so a fresh database gets these tables.

CREATE TABLE IF NOT EXISTS calendar (
	date DATE NOT NULL, 
	day_of_week VARCHAR(3), 
	iso_week INTEGER, 
	month INTEGER, 
	year INTEGER, 
	quarter_idx INTEGER, 
	is_weekend BOOLEAN, 
	is_holiday BOOLEAN, 
	holiday_name VARCHAR(60), 
	is_school_break BOOLEAN, 
	PRIMARY KEY (date)
);


CREATE TABLE IF NOT EXISTS dq_issues (
	id SERIAL NOT NULL, 
	table_name VARCHAR(40) NOT NULL, 
	issue VARCHAR(120) NOT NULL, 
	rows_affected INTEGER NOT NULL, 
	treatment TEXT NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);


CREATE TABLE IF NOT EXISTS model_runs (
	id SERIAL NOT NULL, 
	version VARCHAR(40), 
	trained_at TIMESTAMP WITHOUT TIME ZONE, 
	metrics JSON, 
	PRIMARY KEY (id)
);


CREATE TABLE IF NOT EXISTS promotions (
	promo_id VARCHAR(8) NOT NULL, 
	promo_name VARCHAR(80) NOT NULL, 
	promo_type VARCHAR(20), 
	start_date DATE NOT NULL, 
	end_date DATE NOT NULL, 
	discount_pct FLOAT, 
	department VARCHAR(30), 
	category VARCHAR(40), 
	region_scope VARCHAR(30), 
	expected_uplift_pct FLOAT, 
	is_valid BOOLEAN, 
	PRIMARY KEY (promo_id)
);


CREATE TABLE IF NOT EXISTS sites (
	site_id VARCHAR(8) NOT NULL, 
	site_name VARCHAR(80) NOT NULL, 
	site_type VARCHAR(30) NOT NULL, 
	region VARCHAR(30) NOT NULL, 
	traffic_tier FLOAT NOT NULL, 
	PRIMARY KEY (site_id)
);


CREATE TABLE IF NOT EXISTS suppliers (
	supplier_id VARCHAR(8) NOT NULL, 
	supplier_name VARCHAR(80) NOT NULL, 
	category_focus VARCHAR(40), 
	standard_lead_time_days INTEGER NOT NULL, 
	contract_fill_rate FLOAT, 
	contract_on_time_rate FLOAT, 
	min_order_value FLOAT, 
	delivery_days VARCHAR(30), 
	PRIMARY KEY (supplier_id)
);


CREATE TABLE IF NOT EXISTS products (
	sku VARCHAR(16) NOT NULL, 
	product_name VARCHAR(120) NOT NULL, 
	department VARCHAR(30), 
	category VARCHAR(40), 
	subcategory VARCHAR(40), 
	brand VARCHAR(40), 
	supplier_id VARCHAR(8), 
	unit_cost FLOAT, 
	list_price_base FLOAT, 
	case_size INTEGER, 
	shelf_life_days FLOAT, 
	is_perishable BOOLEAN, 
	storage_type VARCHAR(12), 
	inventory_tracked BOOLEAN NOT NULL, 
	is_seasonal BOOLEAN, 
	launch_date DATE, 
	discontinued_date DATE, 
	status VARCHAR(20), 
	PRIMARY KEY (sku), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (supplier_id)
);

CREATE INDEX IF NOT EXISTS ix_products_category ON products (category);
CREATE INDEX IF NOT EXISTS ix_products_department ON products (department);

CREATE TABLE IF NOT EXISTS promo_performance (
	promo_id VARCHAR(8) NOT NULL, 
	baseline_units_per_store_day FLOAT, 
	promo_units_per_store_day FLOAT, 
	realized_uplift_pct FLOAT, 
	expected_uplift_pct FLOAT, 
	is_valid BOOLEAN, 
	note VARCHAR(160), 
	PRIMARY KEY (promo_id), 
	FOREIGN KEY(promo_id) REFERENCES promotions (promo_id)
);


CREATE TABLE IF NOT EXISTS stores (
	store_id VARCHAR(8) NOT NULL, 
	site_id VARCHAR(8) NOT NULL, 
	store_name VARCHAR(80) NOT NULL, 
	format VARCHAR(20) NOT NULL, 
	operator_type VARCHAR(30), 
	opening_time VARCHAR(5), 
	closing_time VARCHAR(5), 
	opened_date DATE, 
	closed_date DATE, 
	sqft INTEGER, 
	seats INTEGER, 
	PRIMARY KEY (store_id), 
	FOREIGN KEY(site_id) REFERENCES sites (site_id)
);

CREATE INDEX IF NOT EXISTS ix_stores_site_id ON stores (site_id);

CREATE TABLE IF NOT EXISTS supplier_performance (
	supplier_id VARCHAR(8) NOT NULL, 
	po_lines INTEGER, 
	fill_rate FLOAT, 
	on_time_rate FLOAT, 
	contract_fill_rate FLOAT, 
	contract_on_time_rate FLOAT, 
	PRIMARY KEY (supplier_id), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (supplier_id)
);


CREATE TABLE IF NOT EXISTS weather_daily (
	site_id VARCHAR(8) NOT NULL, 
	date DATE NOT NULL, 
	temp_max_c FLOAT, 
	temp_min_c FLOAT, 
	precip_mm FLOAT, 
	PRIMARY KEY (site_id, date), 
	FOREIGN KEY(site_id) REFERENCES sites (site_id)
);


CREATE TABLE IF NOT EXISTS demand_forecast (
	store_id VARCHAR(8) NOT NULL, 
	sku VARCHAR(16) NOT NULL, 
	forecast_date DATE NOT NULL, 
	units FLOAT NOT NULL, 
	lower FLOAT, 
	upper FLOAT, 
	model_version VARCHAR(40), 
	PRIMARY KEY (store_id, sku, forecast_date), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id), 
	FOREIGN KEY(sku) REFERENCES products (sku)
);


CREATE TABLE IF NOT EXISTS inventory_daily (
	date DATE NOT NULL, 
	store_id VARCHAR(8) NOT NULL, 
	sku VARCHAR(16) NOT NULL, 
	opening_qty INTEGER, 
	received_qty INTEGER, 
	units_sold INTEGER, 
	expired_qty INTEGER, 
	damaged_qty INTEGER, 
	closing_qty INTEGER, 
	on_order_qty INTEGER, 
	stockout_hours FLOAT, 
	dq_flag VARCHAR(40), 
	PRIMARY KEY (date, store_id, sku), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id), 
	FOREIGN KEY(sku) REFERENCES products (sku)
);

CREATE INDEX IF NOT EXISTS ix_inv_store_sku ON inventory_daily (store_id, sku, date);

CREATE TABLE IF NOT EXISTS prices (
	price_id VARCHAR(16) NOT NULL, 
	store_id VARCHAR(8) NOT NULL, 
	sku VARCHAR(16) NOT NULL, 
	unit_price FLOAT NOT NULL, 
	currency VARCHAR(3), 
	effective_from DATE, 
	effective_to DATE, 
	price_list VARCHAR(20), 
	PRIMARY KEY (price_id), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id), 
	FOREIGN KEY(sku) REFERENCES products (sku)
);

CREATE INDEX IF NOT EXISTS ix_prices_store_sku ON prices (store_id, sku);

CREATE TABLE IF NOT EXISTS product_barcodes (
	barcode VARCHAR(20) NOT NULL, 
	sku VARCHAR(16) NOT NULL, 
	barcode_type VARCHAR(12), 
	PRIMARY KEY (barcode), 
	FOREIGN KEY(sku) REFERENCES products (sku)
);

CREATE INDEX IF NOT EXISTS ix_product_barcodes_sku ON product_barcodes (sku);

CREATE TABLE IF NOT EXISTS purchase_orders (
	po_id VARCHAR(12) NOT NULL, 
	store_id VARCHAR(8) NOT NULL, 
	supplier_id VARCHAR(8) NOT NULL, 
	sku VARCHAR(16) NOT NULL, 
	order_date DATE NOT NULL, 
	expected_date DATE, 
	received_date DATE, 
	qty_ordered INTEGER NOT NULL, 
	qty_received INTEGER, 
	unit_cost FLOAT, 
	status VARCHAR(16), 
	PRIMARY KEY (po_id), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (supplier_id), 
	FOREIGN KEY(sku) REFERENCES products (sku)
);

CREATE INDEX IF NOT EXISTS ix_purchase_orders_store_id ON purchase_orders (store_id);
CREATE INDEX IF NOT EXISTS ix_purchase_orders_supplier_id ON purchase_orders (supplier_id);

CREATE TABLE IF NOT EXISTS recommendations (
	id SERIAL NOT NULL, 
	store_id VARCHAR(8) NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	priority INTEGER, 
	title VARCHAR(200) NOT NULL, 
	summary VARCHAR(600), 
	est_impact FLOAT, 
	payload JSON, 
	status VARCHAR(12), 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	decided_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_rec_kind CHECK (kind in ('inventory','promotion','staffing')), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id)
);

CREATE INDEX IF NOT EXISTS ix_rec_status ON recommendations (status, kind);
CREATE INDEX IF NOT EXISTS ix_recommendations_store_id ON recommendations (store_id);

CREATE TABLE IF NOT EXISTS sales_daily (
	date DATE NOT NULL, 
	store_id VARCHAR(8) NOT NULL, 
	sku VARCHAR(16) NOT NULL, 
	units_sold FLOAT NOT NULL, 
	unit_price FLOAT, 
	gross_sales FLOAT, 
	discount_amount FLOAT, 
	net_sales FLOAT, 
	promo_id VARCHAR(8), 
	dq_flag VARCHAR(40), 
	PRIMARY KEY (date, store_id, sku), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id), 
	FOREIGN KEY(sku) REFERENCES products (sku), 
	FOREIGN KEY(promo_id) REFERENCES promotions (promo_id)
);

CREATE INDEX IF NOT EXISTS ix_sales_sku ON sales_daily (sku);
CREATE INDEX IF NOT EXISTS ix_sales_store_date ON sales_daily (store_id, date);

CREATE TABLE IF NOT EXISTS staffing_plan (
	store_id VARCHAR(8) NOT NULL, 
	plan_date DATE NOT NULL, 
	forecast_net_sales FLOAT, 
	target_splh FLOAT, 
	required_hours FLOAT, 
	baseline_hours FLOAT, 
	delta_hours FLOAT, 
	status VARCHAR(12), 
	PRIMARY KEY (store_id, plan_date), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id)
);


CREATE TABLE IF NOT EXISTS store_closures (
	store_id VARCHAR(8) NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE NOT NULL, 
	reason VARCHAR(60), 
	PRIMARY KEY (store_id, start_date), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id)
);


CREATE TABLE IF NOT EXISTS store_daily_kpis (
	store_id VARCHAR(8) NOT NULL, 
	date DATE NOT NULL, 
	footfall FLOAT, 
	transactions FLOAT, 
	units_sold FLOAT, 
	net_sales FLOAT, 
	staff_hours FLOAT, 
	open_hours FLOAT, 
	dq_flag VARCHAR(60), 
	PRIMARY KEY (store_id, date), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id)
);


CREATE TABLE IF NOT EXISTS store_forecast (
	store_id VARCHAR(8) NOT NULL, 
	forecast_date DATE NOT NULL, 
	net_sales FLOAT NOT NULL, 
	lower FLOAT, 
	upper FLOAT, 
	model_version VARCHAR(40), 
	PRIMARY KEY (store_id, forecast_date), 
	FOREIGN KEY(store_id) REFERENCES stores (store_id)
);

