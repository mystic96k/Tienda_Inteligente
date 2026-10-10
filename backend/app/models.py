"""Relational schema (PostgreSQL / Cloud SQL; SQLite for tests).

Layers
  1. dimensions  : sites, stores, store_closures, suppliers, products, product_barcodes, calendar,
                   promotions, prices, weather_daily
  2. facts       : sales_daily, inventory_daily, purchase_orders, store_daily_kpis  (cleaned)
  3. governance  : dq_issues (every data-quality problem found + how it was treated)
  4. analytics   : demand_forecast, store_forecast, staffing_plan, promo_performance,
                   supplier_performance, model_runs
  5. decisions   : recommendations
"""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, String, Text)

from .db import Base


# ------------------------------------------------------------------ 1. dimensions
class Site(Base):
    __tablename__ = "sites"
    site_id = Column(String(8), primary_key=True)
    site_name = Column(String(80), nullable=False)
    site_type = Column(String(30), nullable=False)       # Airport / Rail Station / Bus Terminal / Resort
    region = Column(String(30), nullable=False)
    traffic_tier = Column(Float, nullable=False)


class Store(Base):
    __tablename__ = "stores"
    store_id = Column(String(8), primary_key=True)
    site_id = Column(String(8), ForeignKey("sites.site_id"), nullable=False, index=True)
    store_name = Column(String(80), nullable=False)
    format = Column(String(20), nullable=False)          # Full Counter / Express Kiosk / Shop-in-Shop
    operator_type = Column(String(30))
    opening_time = Column(String(5))
    closing_time = Column(String(5))
    opened_date = Column(Date)
    closed_date = Column(Date)                           # NULL = still open
    sqft = Column(Integer)
    seats = Column(Integer)


class StoreClosure(Base):
    __tablename__ = "store_closures"
    store_id = Column(String(8), ForeignKey("stores.store_id"), primary_key=True)
    start_date = Column(Date, primary_key=True)
    end_date = Column(Date, nullable=False)
    reason = Column(String(60))


class Supplier(Base):
    __tablename__ = "suppliers"
    supplier_id = Column(String(8), primary_key=True)
    supplier_name = Column(String(80), nullable=False)
    category_focus = Column(String(40))
    standard_lead_time_days = Column(Integer, nullable=False)
    contract_fill_rate = Column(Float)
    contract_on_time_rate = Column(Float)
    min_order_value = Column(Float)
    delivery_days = Column(String(30))


class Product(Base):
    __tablename__ = "products"
    sku = Column(String(16), primary_key=True)
    product_name = Column(String(120), nullable=False)
    department = Column(String(30), index=True)
    category = Column(String(40), index=True)
    subcategory = Column(String(40))
    brand = Column(String(40))
    supplier_id = Column(String(8), ForeignKey("suppliers.supplier_id"))
    unit_cost = Column(Float)
    list_price_base = Column(Float)
    case_size = Column(Integer, default=1)
    shelf_life_days = Column(Float)
    is_perishable = Column(Boolean)
    storage_type = Column(String(12))
    inventory_tracked = Column(Boolean, nullable=False, default=False)
    is_seasonal = Column(Boolean)
    launch_date = Column(Date)
    discontinued_date = Column(Date)
    status = Column(String(20))


class ProductBarcode(Base):
    __tablename__ = "product_barcodes"
    barcode = Column(String(20), primary_key=True)
    sku = Column(String(16), ForeignKey("products.sku"), nullable=False, index=True)
    barcode_type = Column(String(12))


class Calendar(Base):
    __tablename__ = "calendar"
    date = Column(Date, primary_key=True)
    day_of_week = Column(String(3))
    iso_week = Column(Integer)
    month = Column(Integer)
    year = Column(Integer)
    quarter_idx = Column(Integer)
    is_weekend = Column(Boolean)
    is_holiday = Column(Boolean)
    holiday_name = Column(String(60))
    is_school_break = Column(Boolean)


class Promotion(Base):
    __tablename__ = "promotions"
    promo_id = Column(String(8), primary_key=True)
    promo_name = Column(String(80), nullable=False)
    promo_type = Column(String(20))
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    discount_pct = Column(Float)
    department = Column(String(30))
    category = Column(String(40))
    region_scope = Column(String(30))
    expected_uplift_pct = Column(Float)
    is_valid = Column(Boolean, default=True)             # False when dates are inconsistent (see dq_issues)


class Price(Base):
    __tablename__ = "prices"
    price_id = Column(String(16), primary_key=True)
    store_id = Column(String(8), ForeignKey("stores.store_id"), nullable=False)
    sku = Column(String(16), ForeignKey("products.sku"), nullable=False)
    unit_price = Column(Float, nullable=False)
    currency = Column(String(3), default="USD")
    effective_from = Column(Date)
    effective_to = Column(Date)
    price_list = Column(String(20))
    __table_args__ = (Index("ix_prices_store_sku", "store_id", "sku"),)


class WeatherDaily(Base):
    __tablename__ = "weather_daily"
    site_id = Column(String(8), ForeignKey("sites.site_id"), primary_key=True)
    date = Column(Date, primary_key=True)
    temp_max_c = Column(Float)
    temp_min_c = Column(Float)
    precip_mm = Column(Float)


# ------------------------------------------------------------------ 2. facts
class SalesDaily(Base):
    __tablename__ = "sales_daily"
    date = Column(Date, primary_key=True)
    store_id = Column(String(8), ForeignKey("stores.store_id"), primary_key=True)
    sku = Column(String(16), ForeignKey("products.sku"), primary_key=True)
    units_sold = Column(Float, nullable=False)
    unit_price = Column(Float)
    gross_sales = Column(Float)
    discount_amount = Column(Float, default=0.0)
    net_sales = Column(Float)
    promo_id = Column(String(8), ForeignKey("promotions.promo_id"))
    dq_flag = Column(String(40))
    __table_args__ = (Index("ix_sales_store_date", "store_id", "date"), Index("ix_sales_sku", "sku"))


class InventoryDaily(Base):
    __tablename__ = "inventory_daily"
    date = Column(Date, primary_key=True)
    store_id = Column(String(8), ForeignKey("stores.store_id"), primary_key=True)
    sku = Column(String(16), ForeignKey("products.sku"), primary_key=True)
    opening_qty = Column(Integer)
    received_qty = Column(Integer)
    units_sold = Column(Integer)
    expired_qty = Column(Integer)
    damaged_qty = Column(Integer)
    closing_qty = Column(Integer)
    on_order_qty = Column(Integer)
    stockout_hours = Column(Float)
    dq_flag = Column(String(40))
    __table_args__ = (Index("ix_inv_store_sku", "store_id", "sku", "date"),)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    po_id = Column(String(12), primary_key=True)
    store_id = Column(String(8), ForeignKey("stores.store_id"), nullable=False, index=True)
    supplier_id = Column(String(8), ForeignKey("suppliers.supplier_id"), nullable=False, index=True)
    sku = Column(String(16), ForeignKey("products.sku"), nullable=False)
    order_date = Column(Date, nullable=False)
    expected_date = Column(Date)
    received_date = Column(Date)
    qty_ordered = Column(Integer, nullable=False)
    qty_received = Column(Integer)
    unit_cost = Column(Float)
    status = Column(String(16))


class StoreDailyKpi(Base):
    __tablename__ = "store_daily_kpis"
    store_id = Column(String(8), ForeignKey("stores.store_id"), primary_key=True)
    date = Column(Date, primary_key=True)
    footfall = Column(Float)
    transactions = Column(Float)
    units_sold = Column(Float)
    net_sales = Column(Float)
    staff_hours = Column(Float)
    open_hours = Column(Float)
    dq_flag = Column(String(60))


# ------------------------------------------------------------------ 3. governance
class DqIssue(Base):
    __tablename__ = "dq_issues"
    id = Column(Integer, primary_key=True, autoincrement=True)
    table_name = Column(String(40), nullable=False)
    issue = Column(String(120), nullable=False)
    rows_affected = Column(Integer, nullable=False)
    treatment = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ------------------------------------------------------------------ 4. analytics
class DemandForecast(Base):
    __tablename__ = "demand_forecast"
    store_id = Column(String(8), primary_key=True)
    sku = Column(String(16), primary_key=True)
    forecast_date = Column(Date, primary_key=True)
    units = Column(Float, nullable=False)
    lower = Column(Float)
    upper = Column(Float)
    model_version = Column(String(40))
    __table_args__ = (ForeignKeyConstraint(["store_id"], ["stores.store_id"]),
                      ForeignKeyConstraint(["sku"], ["products.sku"]))


class StoreForecast(Base):
    __tablename__ = "store_forecast"
    store_id = Column(String(8), ForeignKey("stores.store_id"), primary_key=True)
    forecast_date = Column(Date, primary_key=True)
    net_sales = Column(Float, nullable=False)
    lower = Column(Float)
    upper = Column(Float)
    model_version = Column(String(40))


class StaffingPlan(Base):
    __tablename__ = "staffing_plan"
    store_id = Column(String(8), ForeignKey("stores.store_id"), primary_key=True)
    plan_date = Column(Date, primary_key=True)
    forecast_net_sales = Column(Float)
    target_splh = Column(Float)           # sales per labour hour the store achieves at its P60
    required_hours = Column(Float)
    baseline_hours = Column(Float)        # trailing 4 same-weekday average actually staffed
    delta_hours = Column(Float)           # required - baseline (>0 = add)
    status = Column(String(12))           # short / balanced / over


class PromoPerformance(Base):
    __tablename__ = "promo_performance"
    promo_id = Column(String(8), ForeignKey("promotions.promo_id"), primary_key=True)
    baseline_units_per_store_day = Column(Float)
    promo_units_per_store_day = Column(Float)
    realized_uplift_pct = Column(Float)
    expected_uplift_pct = Column(Float)
    is_valid = Column(Boolean)
    note = Column(String(160))


class SupplierPerformance(Base):
    __tablename__ = "supplier_performance"
    supplier_id = Column(String(8), ForeignKey("suppliers.supplier_id"), primary_key=True)
    po_lines = Column(Integer)
    fill_rate = Column(Float)
    on_time_rate = Column(Float)
    contract_fill_rate = Column(Float)
    contract_on_time_rate = Column(Float)


class ModelRun(Base):
    __tablename__ = "model_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(40))
    trained_at = Column(DateTime, default=datetime.utcnow)
    metrics = Column(JSON)


# ------------------------------------------------------------------ 5. decisions
class Recommendation(Base):
    __tablename__ = "recommendations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(String(8), ForeignKey("stores.store_id"), nullable=False, index=True)
    kind = Column(String(12), nullable=False)          # inventory | promotion | staffing
    priority = Column(Integer, default=2)              # 1 critical, 2 high, 3 medium
    title = Column(String(200), nullable=False)
    summary = Column(String(600))
    est_impact = Column(Float, default=0.0)            # USD, expected gain / loss avoided
    payload = Column(JSON)
    status = Column(String(12), default="open")        # open | approved | dismissed
    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime)
    __table_args__ = (CheckConstraint("kind in ('inventory','promotion','staffing')", name="ck_rec_kind"),
                      Index("ix_rec_status", "status", "kind"))


class DailyPlan(Base):
    """What to do, per store and per day: reorder lines, promotions to run, staff to schedule."""
    __tablename__ = "daily_plan"
    id = Column(Integer, primary_key=True, autoincrement=True)
    store_id = Column(String(8), ForeignKey("stores.store_id"), nullable=False)
    plan_date = Column(Date, nullable=False)
    kind = Column(String(8), nullable=False)           # order | promo | staff
    sku = Column(String(16), ForeignKey("products.sku"))
    title = Column(String(200), nullable=False)
    detail = Column(String(400))
    qty = Column(Float)                                # units to order / hours required
    est_impact = Column(Float, default=0.0)            # USD
    priority = Column(Integer, default=2)              # 1 critical, 2 high, 3 medium
    rec_id = Column(Integer)                           # linked recommendation (promotions, order reductions)
    payload = Column(JSON)
    __table_args__ = (CheckConstraint("kind in ('order','promo','staff')", name="ck_plan_kind"),
                      Index("ix_plan_store_date", "store_id", "plan_date"))


class BacktestResult(Base):
    """Replay of the inventory policy over the last 28 days of data (see services/backtest.py)."""
    __tablename__ = "backtest_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    window_start = Column(Date)
    window_end = Column(Date)
    results = Column(JSON)
