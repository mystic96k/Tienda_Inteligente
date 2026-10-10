"""Daily plan: for every store and every day of the 14-day horizon -> what to reorder, what to promote, how many people.

* Reorders are simulated day by day: projected stock = stock - forecast demand + arrivals. An order can only be placed on a day
  whose arrival (order day + supplier lead time) falls on one of the supplier's delivery days. Quantity = order-up-to level that
  covers lead time + the gap to the next delivery (+ safety stock), rounded to full cases.
* Promotions come from the what-if simulation in engine.py (best discount AND best start day).
* Staffing comes from staffing_plan, expressed in hours and in average people on shift.
"""
import math

import numpy as np
import pandas as pd
from scipy.stats import norm
from sqlalchemy import delete, select

from .. import models as m
from ..config import settings
from . import engine as eng

DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
TOP_ORDERS_PER_DAY = 10      # reorder lines shown per store and day (highest impact first)
SERVICE_FLOOR_Z = -9.0       # disabled: tested floors of 60% and 75% in-stock made the backtest worse (waste outweighs margin)
MIN_ORDER_IMPACT = 1.0       # USD of expected lost margin below which an order line is not worth listing


def _arrival_weekdays(delivery_days) -> set:
    s = (str(delivery_days) if isinstance(delivery_days, str) else "Daily").strip().lower()
    if s == "daily":
        return set(range(7))
    if s == "weekly":
        return {0}  # the data does not say which weekday: assume Monday
    out = {DOW[x.strip()[:3]] for x in s.replace(",", "/").split("/") if x.strip()[:3] in DOW}
    return out or set(range(7))


def _gap_to_next(wd: int, allowed: set) -> int:
    for k in range(1, 8):
        if (wd + k) % 7 in allowed:
            return k
    return 7


def decide_order(t, stock, pending, cum, sd, L, gap, shelf, case, margin, min_impact=0.0, cost=None):
    """The ordering policy, shared by the daily plan and the backtest.
    Order-up-to level covering lead time + the gap to the next delivery (+ safety stock). Returns None when no order is
    worth placing, else (qty, expected_short_units, impact_usd, position)."""
    R = min(gap, max(int(shelf), 1))                   # do not buy more than can be sold within shelf life
    cover = L + R
    P = stock + pending
    # stock left over when the order lands is only useful if it will not expire first (age unknown -> be conservative)
    carry = max(0.0, stock - (cum[t + L] - cum[t]))
    ok_days = max(int(shelf) - L, 0)
    usable = min(carry, cum[min(t + L + ok_days, len(cum) - 1)] - cum[min(t + L, len(cum) - 1)]) if ok_days > 0 else 0.0
    P -= carry - usable
    mu = cum[t + cover] - cum[t]
    sigma = sd * math.sqrt(cover)
    if cost is not None and shelf <= cover + 1:
        # perishable: leftovers are written off. Pick the number of cases that minimises expected lost margin + write-off
        # for the window the new batch has to serve (the R days after it lands).
        c = max(cost, 0.01) / (1.0 + max(shelf - R, 0.0))   # part of the leftover still sells on the following days
        avail0 = usable + pending
        muR = cum[min(t + L + R, len(cum) - 1)] - cum[min(t + L, len(cum) - 1)]
        sR = max(sd * math.sqrt(R), 1e-6)

        def exp_cost(S):
            zz = (S - muR) / sR
            sh = sR * (norm.pdf(zz) - zz * (1 - norm.cdf(zz)))
            return margin * sh + c * (S - muR + sh), sh

        best_k, best = 0, exp_cost(avail0)
        for k in range(1, int(math.ceil(max(muR + 3 * sR - avail0, 0) / case)) + 1):
            ec = exp_cost(avail0 + k * case)
            if ec[0] < best[0]:
                best_k, best = k, ec
            else:
                break
        k_floor = int(math.ceil(max(muR + SERVICE_FLOOR_Z * sR - avail0, 0) / case))
        if k_floor > best_k:
            best_k, best = k_floor, exp_cost(avail0 + k_floor * case)
        q = best_k * case
        if q <= 0:
            return None
        short = best[1]
        impact = (exp_cost(avail0)[1] - short) * margin
        return None if impact < min_impact else (q, short, impact, stock + pending)
    q = int(math.ceil(max(0.0, mu + settings.SERVICE_Z * sigma - P) / case) * case)
    if q <= 0:
        return None
    z = (P - mu) / sigma
    short = max(0.0, sigma * (norm.pdf(z) - z * (1 - norm.cdf(z))))
    impact = short * margin
    return None if impact < min_impact else (q, short, impact, P)


def _orders(db, f0, rows):
    last, cur, prod, f, hist = eng._state(db)
    f = f.sort_values(["store_id", "sku", "forecast_date"])
    piv = f.pivot_table(index=["store_id", "sku"], columns="forecast_date", values="units")
    dates = list(piv.columns)
    H = len(dates)
    info = piv.index.to_frame(index=False).merge(cur[["store_id", "sku", "closing_qty", "on_order_qty"]], on=["store_id", "sku"]) \
        .merge(prod, on="sku")
    if info.empty:
        return
    D = piv.loc[list(zip(info.store_id, info.sku))].to_numpy(dtype=float)
    names = dict(zip(prod.sku, prod.product_name))
    pending = []
    for k, r in enumerate(info.itertuples()):
        d = np.nan_to_num(D[k], nan=np.nanmean(D[k]) if np.isfinite(np.nanmean(D[k])) else 0.0).clip(min=0)
        dbar, tail = max(float(d.mean()), 0.05), max(float(d[-7:].mean()), 0.05)
        lead = int(round(r.lead)) if pd.notna(r.lead) else 2
        L = max(1, lead)
        allowed = _arrival_weekdays(r.delivery_days)
        shelf = float(r.shelf_life_days) if pd.notna(r.shelf_life_days) else 30.0
        case = max(int(r.case_size), 1) if pd.notna(r.case_size) else 1
        margin = max(float(r.list_price_base) - float(r.unit_cost), 0.05)
        cum = np.concatenate([[0.0], np.cumsum(np.concatenate([d, np.full(L + 10, tail)]))])
        sd = 1.3 * math.sqrt(dbar + 0.25)
        stock = max(float(r.closing_qty), 0.0) if pd.notna(r.closing_qty) else 0.0
        arrivals = np.zeros(H + L + 2)
        oo = float(r.on_order_qty) if pd.notna(r.on_order_qty) else 0.0
        if oo > 0:
            arrivals[max(0, min(H - 1, int(round(L / 2))))] += oo   # assumption: open orders land halfway through the lead time
        for t in range(H):
            stock += arrivals[t]
            wd = (dates[t] + pd.Timedelta(days=L)).dayofweek
            if wd in allowed:
                dec = decide_order(t, stock, arrivals[t + 1:].sum(), cum, sd, L, _gap_to_next(wd, allowed), shelf, case, margin, MIN_ORDER_IMPACT, float(r.unit_cost))
                if dec:
                    q, short, impact, P = dec
                    stockout = stock + arrivals[t + 1:t + L].sum() - (cum[t + L] - cum[t]) < 0
                    arr = dates[t] + pd.Timedelta(days=L)
                    pending.append(dict(
                        store_id=r.store_id, plan_date=dates[t].date(), kind="order", sku=r.sku,
                        title=f"Pedir {q} × {names.get(r.sku, r.sku)}",
                        detail=f"Llega el {arr:%d/%m} · {r.supplier_name} · cobertura {P / dbar:.1f} d",
                        qty=float(q), est_impact=round(float(impact), 2), priority=1 if stockout else 2, rec_id=None,
                        payload=dict(action="reorder", product=names.get(r.sku, r.sku), order_qty=int(q), case_size=case,
                                     cases=round(q / case, 1), arrives=str(arr.date()), supplier=r.supplier_name,
                                     lead_days=L, delivery_days=r.delivery_days, on_hand_projected=round(stock, 1),
                                     position=round(P, 1), daily_forecast=round(dbar, 2), cover_days=round(P / dbar, 1),
                                     expected_short_units=round(float(short), 1), stockout_risk=bool(stockout))))
                    arrivals[t + L] += q
            stock = max(0.0, stock - d[t])
    df = pd.DataFrame(pending)
    if df.empty:
        return
    df["_r"] = df.groupby(["store_id", "plan_date"]).est_impact.rank(ascending=False, method="first")
    rows.extend(df[df._r <= TOP_ORDERS_PER_DAY].drop(columns="_r").to_dict("records"))


def _reductions(db, f0, rows):
    """'Reduce the next order' recommendations are scheduled on the first order day of the horizon."""
    prod = eng._q(db, "select p.sku, s.standard_lead_time_days lead, s.delivery_days from products p left join suppliers s using(supplier_id)").set_index("sku")
    for r in db.scalars(select(m.Recommendation).where(m.Recommendation.status == "open", m.Recommendation.kind == "inventory")):
        p = r.payload or {}
        if p.get("action") != "reduce_order" or p["sku"] not in prod.index or p.get("skip_units", 0) <= 0:
            continue
        lead, dd = prod.loc[p["sku"], "lead"], prod.loc[p["sku"], "delivery_days"]
        L = max(1, int(round(lead)) if pd.notna(lead) else 2)
        allowed = _arrival_weekdays(dd)
        t0 = next((t for t in range(14) if (f0 + pd.Timedelta(days=t + L)).dayofweek in allowed), 0)
        rows.append(dict(store_id=r.store_id, plan_date=(f0 + pd.Timedelta(days=t0)).date(), kind="order", sku=p["sku"],
                         title=f"Reducir el pedido de {p['product']} en {int(p['skip_units'])} u.",
                         detail=f"Merma probable {p.get('expected_waste_units')} u. · vida útil {p.get('shelf_life_days')} d",
                         qty=float(p["skip_units"]), est_impact=float(r.est_impact), priority=int(r.priority), rec_id=r.id,
                         payload=dict(action="reduce", product=p["product"], skip_units=int(p["skip_units"]),
                                      case_size=p.get("case_size"), on_hand=p.get("on_hand"), on_order=p.get("on_order"),
                                      expected_waste_units=p.get("expected_waste_units"), shelf_life_days=p.get("shelf_life_days"))))


def _promos(db, rows):
    for r in db.scalars(select(m.Recommendation).where(m.Recommendation.status == "open", m.Recommendation.kind == "promotion")):
        p = r.payload or {}
        if "start_date" not in p:
            continue
        start = pd.Timestamp(p["start_date"])
        n = int(p.get("days", 3))
        for i in range(n):
            rows.append(dict(store_id=r.store_id, plan_date=(start + pd.Timedelta(days=i)).date(), kind="promo", sku=p["sku"],
                             title=f"{int(round(p['discount'] * 100))}% de descuento en {p['product']}",
                             detail=f"Día {i + 1} de {n} · {start:%d/%m}–{(start + pd.Timedelta(days=n - 1)):%d/%m}",
                             qty=float(p["discount"]), est_impact=float(r.est_impact) if i == 0 else 0.0,
                             priority=int(r.priority), rec_id=r.id,
                             payload=dict(product=p["product"], discount=p["discount"], day_index=i + 1, days=n,
                                          start_date=p["start_date"], end_date=p["end_date"], on_hand=p.get("on_hand"),
                                          units_with_promo=p.get("units_with_promo"), units_without=p.get("units_without"),
                                          waste_with=p.get("waste_with"), waste_without=p.get("waste_without"))))


def _staff(db, rows):
    sp = eng._q(db, "select store_id, plan_date, forecast_net_sales, target_splh, required_hours, baseline_hours, delta_hours, status from staffing_plan")
    if sp.empty:
        return
    oh = eng._q(db, "select store_id, date, open_hours from store_daily_kpis where open_hours is not null")
    oh = oh.sort_values("date").groupby("store_id").open_hours.apply(lambda x: float(x.tail(60).median())).to_dict()
    for r in sp.itertuples():
        hrs = oh.get(r.store_id) or 12.0
        d = float(r.delta_hours)
        if r.status == "short":
            impact, prio = max(d, 0) * float(r.target_splh) * settings.UNDERSTAFF_LEAKAGE, 1 if d >= 6 else 2
            title = f"Agregar {d:.0f} h de personal"
        elif r.status == "over":
            impact, prio = -d * settings.WAGE_PER_HOUR, 3
            title = f"Reducir {-d:.0f} h de personal"
        else:
            impact, prio, title = 0.0, 3, "Personal equilibrado"
        rows.append(dict(store_id=r.store_id, plan_date=pd.Timestamp(r.plan_date).date(), kind="staff", sku=None, title=title,
                         detail=f"{float(r.required_hours):.0f} h necesarias vs {float(r.baseline_hours):.0f} h habituales",
                         qty=float(r.required_hours), est_impact=round(float(impact), 2), priority=prio, rec_id=None,
                         payload=dict(status=r.status, forecast_sales=round(float(r.forecast_net_sales)), required_hours=round(float(r.required_hours), 1),
                                      baseline_hours=round(float(r.baseline_hours), 1), delta_hours=round(d, 1), open_hours=round(hrs, 1),
                                      people_required=round(float(r.required_hours) / hrs, 1), people_baseline=round(float(r.baseline_hours) / hrs, 1),
                                      target_splh=round(float(r.target_splh), 1))))


def build(db) -> int:
    db.flush()
    db.execute(delete(m.DailyPlan))
    f0 = pd.Timestamp(eng._q(db, "select min(forecast_date) d from demand_forecast").d[0])
    rows: list = []
    _orders(db, f0, rows)
    _reductions(db, f0, rows)
    _promos(db, rows)
    _staff(db, rows)
    db.bulk_insert_mappings(m.DailyPlan, rows)
    return len(rows)
