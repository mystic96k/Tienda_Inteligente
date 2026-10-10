"""CSV -> clean -> database. Every data-quality problem found is counted and logged to `dq_issues`."""
import io
import logging
import os
import re

import httpx
import numpy as np
import pandas as pd
from sqlalchemy import func, select

from .. import models as m
from ..config import settings
from ..db import Base, SessionLocal, engine

log = logging.getLogger("retail.loader")
FILES = ["sites", "stores", "store_closures", "suppliers", "products", "product_barcodes", "calendar",
         "promotions", "prices", "weather_daily", "sales_daily", "inventory_daily", "purchase_orders",
         "store_daily_kpis"]


# ------------------------------------------------------------------ dataset access
def ensure_dataset() -> str:
    d = settings.DATA_DIR
    os.makedirs(d, exist_ok=True)
    for f in FILES:
        path = os.path.join(d, f + ".csv")
        if os.path.exists(path):
            continue
        if not settings.AUTO_DOWNLOAD:
            raise FileNotFoundError(f"{path} missing (AUTO_DOWNLOAD=false). Run scripts/fetch_dataset first.")
        url = f"{settings.DATASET_RAW_URL}/{f}.csv"
        log.info("downloading %s", url)
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with open(path + ".part", "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        os.replace(path + ".part", path)
    return d


def _read(name):
    return pd.read_csv(os.path.join(settings.DATA_DIR, name + ".csv"), dtype=str, keep_default_na=True)


# ------------------------------------------------------------------ normalisers
def norm_store(s):
    s = s.str.strip().str.upper()
    return s.str.replace(r"^S0*(\d+)$", lambda m_: f"S{int(m_.group(1)):03d}", regex=True)


def norm_sku(s):
    s = s.str.strip().str.upper()
    return s.str.replace(r"^SKU-?(\d+)$", r"SKU-\1", regex=True)


def norm_id(s):
    return s.str.strip().str.upper()


def to_date(s):
    return pd.to_datetime(s, format="mixed", errors="coerce").dt.date


def num(s):
    return pd.to_numeric(s, errors="coerce")


def to_int(s):
    return num(s).round().astype("Int64")


def b(s):
    return s.map(lambda v: None if pd.isna(v) else str(v).strip().lower() in ("1", "true", "yes", "y"))


class Dq:
    def __init__(self):
        self.rows = []

    def add(self, table, issue, n, treatment):
        n = int(n)
        if n:
            self.rows.append(m.DqIssue(table_name=table, issue=issue, rows_affected=n, treatment=treatment))
            log.info("DQ %s: %s (%d)", table, issue, n)


# ------------------------------------------------------------------ bulk insert
def bulk(df: pd.DataFrame, model):
    cols = [c.name for c in model.__table__.columns if c.name in df.columns]
    df = df[cols]
    if df.empty:
        return
    if engine.dialect.name == "postgresql":
        buf = io.StringIO()
        df.to_csv(buf, index=False, header=False, na_rep="")
        buf.seek(0)
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cur:
                cur.copy_expert(f"COPY {model.__tablename__} ({','.join(cols)}) FROM STDIN WITH (FORMAT csv, NULL '')", buf)
            raw.commit()
        finally:
            raw.close()
    else:
        df.to_sql(model.__tablename__, engine, if_exists="append", index=False, chunksize=20000)


# ------------------------------------------------------------------ main
def load_all():
    ensure_dataset()
    Base.metadata.create_all(engine)
    dq = Dq()

    sites = _read("sites")
    sites["site_id"] = norm_id(sites.site_id)
    sites["traffic_tier"] = num(sites.traffic_tier)

    stores = _read("stores")
    stores["store_id"], stores["site_id"] = norm_store(stores.store_id), norm_id(stores.site_id)
    for c in ("opened_date", "closed_date"):
        stores[c] = to_date(stores[c])
    for c in ("sqft", "seats"):
        stores[c] = to_int(stores[c])
    stores["store_name"] = stores.store_name.str.strip().str.title()
    if settings.DEMO_STORES:
        stores = stores.head(settings.DEMO_STORES)
    store_ids = set(stores.store_id)
    sites = sites[sites.site_id.isin(set(stores.site_id))]

    clos = _read("store_closures")
    clos["store_id"] = norm_store(clos.store_id)
    clos["start_date"], clos["end_date"] = to_date(clos.start_date), to_date(clos.end_date)
    clos = clos[clos.store_id.isin(store_ids)]

    sup = _read("suppliers")
    sup["supplier_id"] = norm_id(sup.supplier_id)
    for c in ("standard_lead_time_days", "contract_fill_rate", "contract_on_time_rate", "min_order_value"):
        sup[c] = num(sup[c])
    sup["standard_lead_time_days"] = sup.standard_lead_time_days.round().astype("Int64")

    # ---- products
    prod = _read("products")
    prod["sku"] = norm_sku(prod.sku)
    n0 = len(prod)
    prod = prod.drop_duplicates("sku")
    dq.add("products", "duplicate SKU after normalisation", n0 - len(prod), "kept first occurrence")
    for c in ("department", "category", "subcategory"):
        s = prod[c].str.strip()
        canon = s.dropna().groupby(s.dropna().str.lower()).agg(lambda x: x.value_counts().index[0])
        fixed = s.str.lower().map(canon)
        dq.add("products", f"inconsistent casing in {c}", (fixed.notna() & (fixed != prod[c])).sum(), "mapped to the most frequent spelling")
        prod[c] = fixed
    for c in ("unit_cost", "list_price_base", "shelf_life_days"):
        prod[c] = num(prod[c])
    ratio = (prod.unit_cost / prod.list_price_base).groupby(prod.category).transform("median")
    miss = prod.unit_cost.isna() & prod.list_price_base.notna()
    prod.loc[miss, "unit_cost"] = (prod.list_price_base * ratio)[miss].round(2)
    dq.add("products", "missing unit_cost", miss.sum(), "imputed as list price x median cost ratio of its category")
    prod["case_size"] = num(prod.case_size).fillna(1).round().clip(lower=1).astype(int)
    for c in ("is_perishable", "inventory_tracked", "is_seasonal"):
        prod[c] = b(prod[c])
    prod["inventory_tracked"] = prod.inventory_tracked.fillna(False)
    for c in ("launch_date", "discontinued_date"):
        prod[c] = to_date(prod[c])
    prod["supplier_id"] = norm_id(prod.supplier_id)
    skus = set(prod.sku)

    bc = _read("product_barcodes")
    bc["sku"] = norm_sku(bc.sku)
    bc = bc[bc.sku.isin(skus)].drop_duplicates("barcode")

    cal = _read("calendar")
    cal["date"] = to_date(cal.date)
    for c in ("is_weekend", "is_holiday", "is_school_break"):
        cal[c] = b(cal[c])
    for c in ("iso_week", "month", "year", "quarter_idx"):
        cal[c] = to_int(cal[c])

    promo = _read("promotions")
    promo["promo_id"] = norm_id(promo.promo_id)
    promo["start_date"], promo["end_date"] = to_date(promo.start_date), to_date(promo.end_date)
    for c in ("discount_pct", "expected_uplift_pct"):
        promo[c] = num(promo[c])
    promo["is_valid"] = promo.start_date <= promo.end_date
    dq.add("promotions", "end_date before start_date", (~promo.is_valid).sum(),
           "kept but flagged is_valid=false; excluded from promo performance and uplift estimates")
    promo_ids = set(promo.promo_id)

    pr = _read("prices")
    pr["store_id"], pr["sku"] = norm_store(pr.store_id), norm_sku(pr.sku)
    dq.add("prices", "currency written in 5 different ways", (pr.currency != "USD").sum(), "normalised to 'USD'")
    pr["currency"] = "USD"
    pr["unit_price"] = num(pr.unit_price)
    pr["effective_from"], pr["effective_to"] = to_date(pr.effective_from), to_date(pr.effective_to)
    nop = pr.unit_price.isna() | (pr.unit_price <= 0)
    dq.add("prices", "missing or non-positive unit_price", nop.sum(), "dropped")
    pr = pr[~nop & pr.store_id.isin(store_ids) & pr.sku.isin(skus)].drop_duplicates("price_id")

    w = _read("weather_daily")
    w["site_id"], w["date"] = norm_id(w.site_id), to_date(w.date)
    for c in ("temp_max_c", "temp_min_c", "precip_mm"):
        w[c] = num(w[c])
    w = w[w.site_id.isin(set(sites.site_id))].drop_duplicates(["site_id", "date"])

    # ---- sales
    s = _read("sales_daily")
    n_raw = len(s)
    dq.add("sales_daily", "dates not in ISO format (e.g. 27-Jan-26)", (~s.date.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)).sum(),
           "parsed with a mixed-format parser")
    s["date"] = to_date(s.date)
    raw_store, raw_sku = s.store_id.copy(), s.sku.copy()
    s["store_id"], s["sku"] = norm_store(s.store_id), norm_sku(s.sku)
    dq.add("sales_daily", "store_id written with spaces / lower case / extra zeros (e.g. 'S0032', 's013 ')", (raw_store != s.store_id).sum(), "normalised to S###")
    dq.add("sales_daily", "SKU written without hyphen / with spaces / lower case", (raw_sku != s.sku).sum(), "normalised to SKU-######")
    for c in ("units_sold", "unit_price", "gross_sales", "discount_amount", "net_sales"):
        s[c] = num(s[c])
    s["promo_id"] = norm_id(s.promo_id)
    s.loc[~s.promo_id.isin(promo_ids), "promo_id"] = None
    n = len(s)
    s = s.drop_duplicates()
    dq.add("sales_daily", "exact duplicate rows", n - len(s), "removed")
    n = len(s)
    s = s.drop_duplicates(["date", "store_id", "sku"])
    dq.add("sales_daily", "same date/store/sku twice with different values", n - len(s), "kept first")
    bad = ~s.store_id.isin(store_ids) | ~s.sku.isin(skus) | s.date.isna()
    dq.add("sales_daily", "store or SKU not in master data (unknown SKUs / test codes)", bad.sum(), "quarantined (not loaded)")
    s = s[~bad].copy()
    s["dq_flag"] = None
    imp = s.units_sold.isna() & s.unit_price.gt(0) & s.gross_sales.notna()
    s.loc[imp, "units_sold"] = (s.gross_sales / s.unit_price).round()[imp]
    s.loc[imp, "dq_flag"] = "units_imputed"
    dq.add("sales_daily", "missing units_sold", imp.sum(), "recomputed as gross_sales / unit_price")
    s = s[s.units_sold.notna()]
    ret = s.units_sold < 0
    s.loc[ret, "dq_flag"] = "return"
    dq.add("sales_daily", "negative units (returns / reversals)", ret.sum(), "kept, flagged 'return', excluded from demand and uplift")
    mis = ((s.gross_sales - s.discount_amount.fillna(0) - s.net_sales).abs() > 0.02)
    s.loc[mis, "net_sales"] = (s.gross_sales - s.discount_amount.fillna(0))[mis].round(2)
    s.loc[mis & s.dq_flag.isna(), "dq_flag"] = "net_recomputed"
    dq.add("sales_daily", "net_sales != gross - discount", mis.sum(), "net_sales recomputed")

    # ---- inventory
    iv = _read("inventory_daily")
    iv["date"], iv["store_id"], iv["sku"] = to_date(iv.date), norm_store(iv.store_id), norm_sku(iv.sku)
    for c in ("opening_qty", "received_qty", "units_sold", "expired_qty", "damaged_qty", "closing_qty", "on_order_qty", "stockout_hours"):
        iv[c] = num(iv[c])
    n = len(iv)
    iv = iv.drop_duplicates(["date", "store_id", "sku"])
    dq.add("inventory_daily", "duplicate day/store/SKU rows", n - len(iv), "removed")
    bad = ~iv.store_id.isin(store_ids) | ~iv.sku.isin(skus) | iv.date.isna()
    dq.add("inventory_daily", "store or SKU not in master data", bad.sum(), "quarantined")
    iv = iv[~bad].copy()
    iv["dq_flag"] = None
    ident = (iv.opening_qty + iv.received_qty - iv.units_sold - iv.expired_qty - iv.damaged_qty - iv.closing_qty).abs() > 0
    iv.loc[ident, "dq_flag"] = "identity_mismatch"
    dq.add("inventory_daily", "closing != opening + received - sold - expired - damaged", ident.sum(), "kept as reported, flagged")
    neg = iv.closing_qty < 0
    iv.loc[neg, "closing_qty"] = 0
    iv.loc[neg, "dq_flag"] = "neg_closing"
    dq.add("inventory_daily", "negative closing_qty", neg.sum(), "clipped to 0, flagged")
    for c in ("opening_qty", "received_qty", "units_sold", "expired_qty", "damaged_qty", "closing_qty", "on_order_qty"):
        iv[c] = iv[c].round().astype("Int64")

    # ---- purchase orders
    po = _read("purchase_orders")
    po["store_id"], po["sku"], po["supplier_id"] = norm_store(po.store_id), norm_sku(po.sku), norm_id(po.supplier_id)
    for c in ("order_date", "expected_date", "received_date"):
        po[c] = to_date(po[c])
    for c in ("qty_ordered", "qty_received", "unit_cost"):
        po[c] = num(po[c])
    n = len(po)
    po = po.drop_duplicates("po_id", keep="last")
    dq.add("purchase_orders", "repeated po_id", n - len(po), "kept last occurrence")
    bad = ~po.store_id.isin(store_ids) | ~po.sku.isin(skus) | ~po.supplier_id.isin(set(sup.supplier_id))
    dq.add("purchase_orders", "store, SKU or supplier not in master data", bad.sum(), "quarantined")
    po = po[~bad].copy()
    po["qty_ordered"] = po.qty_ordered.round().astype("Int64")
    po["qty_received"] = po.qty_received.round().astype("Int64")

    # ---- store KPIs
    k = _read("store_daily_kpis")
    k["store_id"], k["date"] = norm_store(k.store_id), to_date(k.date)
    for c in ("footfall", "transactions", "units_sold", "net_sales", "staff_hours", "open_hours"):
        k[c] = num(k[c])
    n = len(k)
    k = k.drop_duplicates(["store_id", "date"])
    dq.add("store_daily_kpis", "duplicate store/day rows", n - len(k), "removed")
    k = k[k.store_id.isin(store_ids)].copy()
    k["dq_flag"] = None
    bad_sh = (k.staff_hours <= 0) | (k.staff_hours > 300)
    k.loc[bad_sh, "staff_hours"] = np.nan
    k.loc[bad_sh, "dq_flag"] = "staff_hours_invalid"
    dq.add("store_daily_kpis", "staff_hours impossible (<=0 or 999 sentinel)", bad_sh.sum(), "set to NULL; imputed later with store/weekday median")
    z = k.footfall.fillna(0) == 0
    k.loc[z, "footfall"] = np.nan
    dq.add("store_daily_kpis", "footfall missing or 0 on an open day", z.sum(), "set to NULL")
    med = k.groupby("store_id").transactions.transform("median")
    tx_bad = (k.transactions > 3 * med) | (k.transactions > k.footfall)
    k.loc[tx_bad, "transactions"] = np.nan
    k.loc[tx_bad, "dq_flag"] = k.dq_flag.fillna("") + "tx_outlier"
    dq.add("store_daily_kpis", "transactions > footfall or > 3x store median (scale error)", tx_bad.sum(),
           "set to NULL; net_sales (clean) is the forecasting target")

    # ---- write
    with SessionLocal() as db:
        if db.scalar(select(func.count(m.Site.site_id))):
            return
    for df, model in [(sites, m.Site), (stores, m.Store), (clos, m.StoreClosure), (sup, m.Supplier), (prod, m.Product),
                      (bc, m.ProductBarcode), (cal, m.Calendar), (promo, m.Promotion), (pr, m.Price), (w, m.WeatherDaily),
                      (s, m.SalesDaily), (iv, m.InventoryDaily), (po, m.PurchaseOrder), (k, m.StoreDailyKpi)]:
        log.info("loading %s (%d rows)", model.__tablename__, len(df))
        bulk(df, model)
    with SessionLocal() as db:
        db.add_all(dq.rows)
        db.commit()
    log.info("raw sales rows %d -> loaded %d", n_raw, len(s))
