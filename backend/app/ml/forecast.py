"""Two global gradient-boosted models (Poisson loss, direct 14-day horizon).

  * store model : daily net sales per store          -> revenue outlook + staffing
  * sku model   : daily demand per store x tracked SKU -> reorder / waste / markdown
                  target is *uncensored* demand: units / (1 - share of open hours spent out of stock)

All lag / rolling features are shifted >= 14 days, so training rows and future rows are built identically
(no recursive forecasting). Promo depth and weather are features -> the sku model supports what-if.
"""
import os
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from sqlalchemy import text

from .. import models as m
from ..config import settings

H = 14
_cache: dict = {}


# ------------------------------------------------------------------ shared helpers
def _q(db, sql):
    return pd.read_sql(text(sql), db.bind)


def _dates(df, *cols):
    for c in cols:
        df[c] = pd.to_datetime(df[c])
    return df


def _calendar(db, dates):
    cal = _dates(_q(db, "select date,is_weekend,is_holiday,is_school_break from calendar"), "date").set_index("date")
    out = pd.DataFrame({"date": dates})
    out["dow"], out["month"] = out.date.dt.dayofweek, out.date.dt.month
    known = out.date.isin(cal.index)
    out["is_holiday"] = out.date.map(cal.is_holiday).astype(float)
    out["is_school_break"] = out.date.map(cal.is_school_break).astype(float)
    hol = set(USFederalHolidayCalendar().holidays(out.date.min(), out.date.max()))
    prev = (out.date - pd.Timedelta(days=364)).map(cal.is_school_break).astype(float)
    out.loc[~known, "is_holiday"] = out.loc[~known, "date"].isin(hol).astype(float)
    out.loc[~known, "is_school_break"] = prev[~known]
    return out.fillna({"is_holiday": 0.0, "is_school_break": 0.0})


def _weather(db, dates):
    w = _dates(_q(db, "select site_id,date,temp_max_c,precip_mm from weather_daily"), "date")
    last = w.date.max()
    fut = [d for d in dates if d > last]
    if fut:                                  # climatology proxy: same calendar day one year earlier
        base = pd.DataFrame({"date": fut})
        base["src"] = base.date - pd.Timedelta(days=365)
        extra = base.merge(w.rename(columns={"date": "src"}), on="src")[["site_id", "date", "temp_max_c", "precip_mm"]]
        w = pd.concat([w, extra], ignore_index=True)
    return w


def _lags(df, key, y):
    g = df.groupby(key, sort=False)[y]
    for n in (14, 21, 28):
        df[f"lag{n}"] = g.shift(n)
    sh = g.shift(14)
    gs = sh.groupby(df[key], sort=False)
    df["roll7"] = gs.rolling(7, min_periods=4).mean().reset_index(level=0, drop=True)
    df["roll28"] = gs.rolling(28, min_periods=14).mean().reset_index(level=0, drop=True)
    return df


def _model(cat_mask):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(loss="poisson", learning_rate=0.08, max_iter=250, max_depth=8,
                                         categorical_features=cat_mask, random_state=0)


def _wape(y, p):
    return float(np.abs(y - p).sum() / max(y.sum(), 1e-9))


# ------------------------------------------------------------------ store model
STORE_F = ["store_idx", "format_idx", "tier", "dow", "month", "is_holiday", "is_school_break", "temp_max_c", "precip_mm",
           "lag14", "lag21", "lag28", "roll7", "roll28"]
STORE_CAT = [True, True] + [False] * (len(STORE_F) - 2)


def _store_panel(db):
    k = _dates(_q(db, "select store_id,date,net_sales,open_hours from store_daily_kpis"), "date")
    st = _q(db, "select s.store_id,s.site_id,s.format,s.closed_date,si.traffic_tier from stores s join sites si using(site_id)")
    last = k.date.max()
    dates = pd.date_range(k.date.min(), last + pd.Timedelta(days=H))
    grid = pd.MultiIndex.from_product([st.store_id, dates], names=["store_id", "date"]).to_frame(index=False)
    df = grid.merge(k[["store_id", "date", "net_sales"]], how="left", on=["store_id", "date"]).merge(st, on="store_id")
    df = df.merge(_calendar(db, dates), on="date").merge(_weather(db, dates), how="left", on=["site_id", "date"])
    df["store_idx"] = df.store_id.str[1:].astype(int)
    df["format_idx"] = df.format.astype("category").cat.codes
    df["tier"] = df.traffic_tier
    df = df.sort_values(["store_id", "date"]).reset_index(drop=True)
    df = _lags(df, "store_id", "net_sales")
    return df, last, st


def train_store(db):
    df, last, st = _store_panel(db)
    hist = df[df.net_sales.notna() & df.roll28.notna() & df.lag28.notna()]
    cut = last - pd.Timedelta(days=28)
    tr, va = hist[hist.date <= cut], hist[hist.date > cut]
    mdl = _model(STORE_CAT).fit(tr[STORE_F], tr.net_sales)
    pred = mdl.predict(va[STORE_F])
    met = dict(wape_model=round(_wape(va.net_sales.values, pred), 4), wape_naive=round(_wape(va.net_sales.values, va.roll28.values), 4),
               rows=int(len(tr)))
    resid = pd.Series(va.net_sales.values - pred, index=va.store_id.values).groupby(level=0).std().fillna(0)
    final = _model(STORE_CAT).fit(hist[STORE_F], hist.net_sales)
    return final, resid.to_dict(), met


def forecast_store(db, model, sigma, version):
    df, last, st = _store_panel(db)
    fut = df[(df.date > last) & (df.closed_date.isna() | (pd.to_datetime(df.closed_date) > last))].copy()
    fut["yhat"] = np.clip(model.predict(fut[STORE_F]), 0, None)
    fut["sd"] = fut.store_id.map(sigma).fillna(0)
    rows = [dict(store_id=r.store_id, forecast_date=r.date.date(), net_sales=float(r.yhat),
                 lower=float(max(r.yhat - 1.28 * r.sd, 0)), upper=float(r.yhat + 1.28 * r.sd), model_version=version)
            for r in fut.itertuples()]
    db.execute(text("delete from store_forecast"))
    db.bulk_insert_mappings(m.StoreForecast, rows)
    db.commit()


# ------------------------------------------------------------------ sku model
SKU_F = ["store_idx", "sku_idx", "cat_idx", "tier", "dow", "month", "is_holiday", "is_school_break", "temp_max_c", "precip_mm",
         "price", "shelf", "discount_pct", "lag14", "lag21", "lag28", "roll7", "roll28"]
SKU_CAT = [True, True, True] + [False] * (len(SKU_F) - 3)


def _sku_panel(db):
    iv = _dates(_q(db, """select i.date,i.store_id,i.sku,i.units_sold,i.stockout_hours from inventory_daily i
                          join products p using(sku) where p.inventory_tracked = true"""), "date")
    iv = iv.rename(columns={"units_sold": "units"})
    oh = _q(db, "select store_id, avg(open_hours) as oh from store_daily_kpis group by store_id").set_index("store_id").oh
    share = (iv.stockout_hours.fillna(0) / iv.store_id.map(oh).fillna(18)).clip(0, 0.5)
    iv["demand"] = iv.units / (1 - share)
    disc = _dates(_q(db, """select date,store_id,sku, sum(discount_amount)/nullif(sum(gross_sales),0) as discount_pct
                            from sales_daily where dq_flag is null or dq_flag <> 'return' group by date,store_id,sku"""), "date")
    pr = _q(db, "select sku, list_price_base as price, shelf_life_days as shelf, category from products")
    st = _q(db, "select s.store_id,s.site_id,s.closed_date,si.traffic_tier as tier from stores s join sites si using(site_id)")
    last = iv.date.max()
    dates = pd.date_range(iv.date.min(), last + pd.Timedelta(days=H))
    series = iv[["store_id", "sku"]].drop_duplicates().reset_index(drop=True)
    series["sid"] = series.index
    grid = series.assign(_k=1).merge(pd.DataFrame({"date": dates, "_k": 1}), on="_k").drop(columns="_k")
    df = grid.merge(iv[["date", "store_id", "sku", "demand", "units"]], how="left", on=["date", "store_id", "sku"])
    df = df.merge(disc, how="left", on=["date", "store_id", "sku"]).merge(pr, on="sku").merge(st, on="store_id")
    df["discount_pct"] = df.discount_pct.fillna(0).clip(0, 0.6)
    df = df.merge(_calendar(db, dates), on="date").merge(_weather(db, dates), how="left", on=["site_id", "date"])
    df.loc[df.date > last, "discount_pct"] = 0.0
    df["store_idx"] = df.store_id.str[1:].astype(int)
    df["sku_idx"] = df.sku.str[4:].astype(int)
    df["cat_idx"] = df.category.astype("category").cat.codes
    df["shelf"] = df.shelf.fillna(30)
    df = df.sort_values(["sid", "date"]).reset_index(drop=True)
    df = _lags(df, "sid", "demand")
    return df, last


def train_sku(db):
    df, last = _sku_panel(db)
    hist = df[df.demand.notna() & df.roll28.notna() & df.lag28.notna()]
    cut = last - pd.Timedelta(days=28)
    tr, va = hist[hist.date <= cut], hist[hist.date > cut]
    mdl = _model(SKU_CAT).fit(tr[SKU_F], tr.demand)
    pred = mdl.predict(va[SKU_F])
    met = dict(wape_model=round(_wape(va.demand.values, pred), 4), wape_naive=round(_wape(va.demand.values, va.roll28.values), 4),
               rows=int(len(tr)), series=int(hist.sid.nunique()))
    final = _model(SKU_CAT).fit(hist[SKU_F], hist.demand)
    return final, met


def forecast_sku(db, model, version):
    df, last = _sku_panel(db)
    recent = df[(df.date <= last) & (df.date > last - pd.Timedelta(days=28))].groupby("sid").units.sum()
    active = set(recent[recent > 0].index)
    fut = df[(df.date > last) & df.sid.isin(active) & (df.closed_date.isna() | (pd.to_datetime(df.closed_date) > last))].copy()
    fut["yhat"] = np.clip(model.predict(fut[SKU_F]), 0, None)
    fut["sd"] = 1.3 * np.sqrt(fut.yhat + 0.25)
    db.execute(text("delete from demand_forecast"))
    rows = [dict(store_id=r.store_id, sku=r.sku, forecast_date=r.date.date(), units=float(r.yhat),
                 lower=float(max(r.yhat - 1.28 * r.sd, 0)), upper=float(r.yhat + 1.28 * r.sd), model_version=version)
            for r in fut.itertuples()]
    db.bulk_insert_mappings(m.DemandForecast, rows)
    db.commit()
    _cache["future"] = fut.drop(columns=["yhat", "sd"])
    return fut


# ------------------------------------------------------------------ orchestration
def train_all(db) -> dict:
    version = f"hgb-poisson-{datetime.utcnow():%Y%m%d%H%M}"
    smodel, sigma, smet = train_store(db)
    kmodel, kmet = train_sku(db)
    os.makedirs(settings.MODEL_DIR, exist_ok=True)
    joblib.dump({"store": smodel, "sigma": sigma, "sku": kmodel, "version": version},
                os.path.join(settings.MODEL_DIR, "models.joblib"))
    _cache.clear()
    metrics = {"store": smet, "sku": kmet, "horizon_days": H, "version": version}
    for k in ("store", "sku"):
        v = metrics[k]
        v["improvement_vs_naive_pct"] = round(100 * (1 - v["wape_model"] / max(v["wape_naive"], 1e-9)), 1)
    db.add(m.ModelRun(version=version, metrics=metrics))
    db.commit()
    return metrics


def bundle():
    if "bundle" not in _cache:
        p = os.path.join(settings.MODEL_DIR, "models.joblib")
        if not os.path.exists(p):
            return None
        _cache["bundle"] = joblib.load(p)
    return _cache["bundle"]


def forecast_all(db):
    b = bundle()
    forecast_store(db, b["store"], b["sigma"], b["version"])
    forecast_sku(db, b["sku"], b["version"])


def future_sku(db):
    if "future" not in _cache:
        forecast_sku(db, bundle()["sku"], bundle()["version"])
    return _cache["future"]


def whatif(db, keys, discount, promo_days):
    """Predicted daily units for [(store_id, sku), ...] if `discount` runs on the first `promo_days` of the horizon."""
    f = future_sku(db)
    kk = pd.DataFrame(keys, columns=["store_id", "sku"])
    s = f.merge(kk, on=["store_id", "sku"]).sort_values(["sid", "date"]).copy()
    s["rank"] = s.groupby("sid").cumcount()
    s["discount_pct"] = np.where(s["rank"] < promo_days, discount, 0.0)
    s["pred"] = np.clip(bundle()["sku"].predict(s[SKU_F]), 0, None)
    return s[["store_id", "sku", "rank", "pred"]]
