"""Decision engine: forecasts + current stock/roster -> ranked, quantified recommendations (USD)."""
import math

import numpy as np
import pandas as pd
from scipy.stats import norm
from sqlalchemy import delete, text

from .. import models as m
from ..config import settings
from ..ml import forecast as fc

GRID = [0.10, 0.15, 0.20, 0.30, 0.40]
TOP_INV, TOP_PROMO = 14, 8


def _q(db, sql):
    return pd.read_sql(text(sql), db.bind)


def run_all(db) -> dict:
    fc.forecast_all(db)
    db.execute(delete(m.Recommendation).where(m.Recommendation.status == "open"))
    db.commit()
    n_inv, n_promo = inventory_and_promotions(db)
    n_staff = staffing(db)
    promo_performance(db)
    supplier_performance(db)
    db.commit()
    return {"inventory": n_inv, "promotion": n_promo, "staffing": n_staff}


# ------------------------------------------------------------------ inventory + promotions
def _review_days(delivery_days: str) -> float:
    s = (delivery_days or "Daily").strip().lower()
    if s == "daily":
        return 1.0
    n = len([x for x in s.replace(",", "/").split("/") if x.strip()])
    return 7.0 / max(n, 1)


def _state(db):
    last = pd.to_datetime(_q(db, "select max(date) d from inventory_daily").d[0])
    cur = _q(db, f"""select i.store_id,i.sku,i.closing_qty,i.on_order_qty,i.date from inventory_daily i
                     join (select store_id,sku,max(date) md from inventory_daily group by store_id,sku) x
                       on x.store_id=i.store_id and x.sku=i.sku and x.md=i.date""")
    cur["date"] = pd.to_datetime(cur.date)
    cur = cur[cur.date >= last - pd.Timedelta(days=3)]
    prod = _q(db, """select p.sku,p.product_name,p.category,p.unit_cost,p.list_price_base,p.case_size,p.shelf_life_days,
                     s.standard_lead_time_days lead,s.delivery_days,s.supplier_name
                     from products p left join suppliers s using(supplier_id)""")
    f = _q(db, "select store_id,sku,forecast_date,units from demand_forecast")
    f["forecast_date"] = pd.to_datetime(f.forecast_date)
    hist = _q(db, f"""select store_id,sku,sum(expired_qty) expired,sum(received_qty) received,avg(stockout_hours) so_h
                      from inventory_daily where date > '{(last - pd.Timedelta(days=28)).date()}' group by store_id,sku""")
    return last, cur, prod, f, hist


def inventory_and_promotions(db):
    last, cur, prod, f, hist = _state(db)
    names = _q(db, "select store_id,store_name from stores").set_index("store_id").store_name
    d7 = f[f.forecast_date <= f.forecast_date.min() + pd.Timedelta(days=6)].groupby(["store_id", "sku"]).units.mean().rename("d")
    df = cur.merge(d7.reset_index(), on=["store_id", "sku"]).merge(prod, on="sku").merge(hist, on=["store_id", "sku"], how="left")
    df["d"] = df.d.clip(lower=0.05)
    df["lead"] = df.lead.fillna(2)
    df["review"] = df.delivery_days.map(_review_days)
    df["W"] = df.lead + df.review
    df["margin"] = (df.list_price_base - df.unit_cost).clip(lower=0.05)
    df["sd"] = 1.3 * np.sqrt(df.d + 0.25)
    df["x"] = df.closing_qty.clip(lower=0) + df.on_order_qty.fillna(0)
    df["mu"], df["sigma"] = df.d * df.W, df.sd * np.sqrt(df.W)
    z = (df.x - df.mu) / df.sigma
    df["short"] = (df.sigma * (norm.pdf(z) - z * (1 - norm.cdf(z)))).clip(lower=0)
    shelf = df.shelf_life_days.fillna(30)
    df["target"] = np.minimum(df.mu + settings.SERVICE_Z * df.sigma, df.d * np.maximum(shelf, df.W) + settings.SERVICE_Z * df.sigma)
    df["qty_raw"] = (df.target - df.x).clip(lower=0)
    df["qty"] = (np.ceil(df.qty_raw / df.case_size) * df.case_size).astype(int)
    df["waste_units"] = np.where(shelf <= 14, (df.x - df.d * shelf).clip(lower=0), 0.0)
    df["waste_usd"] = df.waste_units * df.unit_cost
    df["short_usd"] = df.short * df.margin
    recs = []

    inv = df[(df.short_usd >= 2.0) & (df.qty > 0)].copy()
    inv["prio"] = np.where(inv.closing_qty < inv.d, 1, 2)
    for r in inv.sort_values("short_usd", ascending=False).groupby("store_id").head(TOP_INV).itertuples():
        recs.append(m.Recommendation(
            store_id=r.store_id, kind="inventory", priority=int(r.prio),
            title=f"Pedir {r.qty} × {r.product_name}",
            summary=f"{r.closing_qty:.0f} en tienda + {r.on_order_qty:.0f} en camino vs demanda de {r.d:.1f}/día; "
                    f"proveedor {r.supplier_name} ({r.lead:.0f} d de entrega, reparto {r.delivery_days}). "
                    f"Riesgo de quiebre en la ventana de {r.W:.0f} d.",
            est_impact=round(float(r.short_usd), 2),
            payload=dict(sku=r.sku, product=r.product_name, action="reorder", order_qty=int(r.qty), case_size=int(r.case_size),
                         on_hand=float(r.closing_qty), on_order=float(r.on_order_qty), daily_forecast=round(float(r.d), 2),
                         cover_days=round(float(r.x / r.d), 1), lead_time=float(r.lead), expected_short_units=round(float(r.short), 1),
                         stockout_hours_28d=None if pd.isna(r.so_h) else round(float(r.so_h), 1))))
    n_inv = len(recs)

    waste = df[(df.waste_usd >= 2.0) & (df.shelf_life_days <= 14)].copy()
    for r in waste.sort_values("waste_usd", ascending=False).groupby("store_id").head(TOP_INV).itertuples():
        skip = int(math.floor(r.waste_units / r.case_size) * r.case_size)
        recs.append(m.Recommendation(
            store_id=r.store_id, kind="inventory", priority=2 if r.waste_usd >= 10 else 3,
            title=f"Reducir el próximo pedido de {r.product_name}",
            summary=f"Hay {r.x:.0f} unidades (en tienda + en camino) y se venden ~{r.d * r.shelf_life_days:.0f} en su vida útil de "
                    f"{r.shelf_life_days:.0f} d. Se estiman {r.waste_units:.0f} unidades de merma (USD {r.waste_usd:,.0f}).",
            est_impact=round(float(r.waste_usd), 2),
            payload=dict(sku=r.sku, product=r.product_name, action="reduce_order", skip_units=skip, case_size=int(r.case_size),
                         on_hand=float(r.closing_qty), on_order=float(r.on_order_qty), daily_forecast=round(float(r.d), 2),
                         shelf_life_days=float(r.shelf_life_days), expected_waste_units=round(float(r.waste_units), 1),
                         expired_28d=None if pd.isna(r.expired) else float(r.expired))))
    n_inv = len(recs)

    # ---- markdown promotions via what-if on the demand model
    cand = waste.sort_values("waste_usd", ascending=False).head(400)
    n_promo = 0
    if len(cand):
        keys = list(zip(cand.store_id, cand.sku))
        horizon = {}
        for dsc in [0.0] + GRID:
            w = fc.whatif(db, keys, dsc, promo_days=3)
            horizon[dsc] = {k: g.sort_values("rank").pred.values for k, g in w.groupby(["store_id", "sku"])}
        out = []
        for r in cand.itertuples():
            key, e = (r.store_id, r.sku), max(1, int(round(r.shelf_life_days / 2)))
            best, base = None, None
            for dsc in [0.0] + GRID:
                u = horizon[dsc].get(key)
                if u is None:
                    continue
                price = np.array([r.list_price_base * (1 - dsc) if i < 3 else r.list_price_base for i in range(len(u))])
                val = _outcome(u, r.x, price, r.unit_cost, e)
                if dsc == 0.0:
                    base = val
                elif best is None or val[0] > best[0][0]:
                    best = (val, dsc)
            if base and best and best[0][0] - base[0] >= 2.0:
                out.append((best[0][0] - base[0], r, best[1], best[0], base))
        by_store = {}
        for gain, r, dsc, val, base in sorted(out, key=lambda t: -t[0]):
            if by_store.get(r.store_id, 0) >= TOP_PROMO:
                continue
            by_store[r.store_id] = by_store.get(r.store_id, 0) + 1
            recs.append(m.Recommendation(
                store_id=r.store_id, kind="promotion", priority=1 if gain >= 10 else 2,
                title=f"{int(dsc * 100)}% de descuento en {r.product_name}",
                summary=f"Liquidar {r.x:.0f} unidades antes de caducar (vida útil {r.shelf_life_days:.0f} d). Simulación: "
                        f"{val[1]:.0f} vendidas con promo vs {base[1]:.0f} sin ella; merma {val[2]:.0f} vs {base[2]:.0f} unidades.",
                est_impact=round(float(gain), 2),
                payload=dict(sku=r.sku, product=r.product_name, discount=dsc, days=3, on_hand=float(r.x),
                             units_with_promo=round(float(val[1]), 1), units_without=round(float(base[1]), 1),
                             waste_with=round(float(val[2]), 1), waste_without=round(float(base[2]), 1),
                             shelf_life_days=float(r.shelf_life_days))))
            n_promo += 1
    db.add_all(recs)
    return n_inv, n_promo


def _outcome(units, stock, price, cost, expiry_day):
    """Profit over horizon - carrying cost on leftovers - write-off of units that expire."""
    sold, st, profit, waste = 0.0, float(stock), 0.0, 0.0
    for i, (u, p) in enumerate(zip(units, price)):
        if i == expiry_day:
            waste, st = st, 0.0
        s = min(st, u)
        st -= s
        sold += s
        profit += s * (p - cost)
    profit -= waste * cost + st * cost * settings.CARRY_COST_PER_DAY * len(units)
    return profit, sold, waste


# ------------------------------------------------------------------ staffing
def staffing(db) -> int:
    db.execute(delete(m.StaffingPlan))
    k = _q(db, "select store_id,date,net_sales,staff_hours,open_hours from store_daily_kpis")
    k["date"] = pd.to_datetime(k.date)
    last = k.date.max()
    stn = _q(db, "select store_id,store_name,closed_date from stores").set_index("store_id")
    f = _q(db, "select store_id,forecast_date,net_sales from store_forecast")
    f["forecast_date"] = pd.to_datetime(f.forecast_date)
    rows, recs = [], []
    for sid, g in k.groupby("store_id"):
        fs = f[f.store_id == sid]
        if fs.empty:
            continue
        ok = g[g.staff_hours.notna() & (g.date > last - pd.Timedelta(days=120))]
        if len(ok) < 30:
            continue
        splh = (ok.net_sales / ok.staff_hours)
        target = float(splh.quantile(settings.SPLH_PERCENTILE))
        oh = float(g.open_hours.tail(60).median())
        base_by_dow = ok[ok.date > last - pd.Timedelta(days=28)].assign(dow=lambda x: x.date.dt.dayofweek).groupby("dow").staff_hours.mean()
        fallback = float(ok.staff_hours.tail(28).mean())
        short, over = [], []
        for r in fs.itertuples():
            base = float(base_by_dow.get(r.forecast_date.dayofweek, fallback))
            req = max(r.net_sales / target, settings.MIN_STAFF_RATIO * oh)
            delta = req - base
            st = "short" if delta > settings.STAFF_TOLERANCE * base else "over" if delta < -settings.STAFF_TOLERANCE * base else "balanced"
            rows.append(dict(store_id=sid, plan_date=r.forecast_date.date(), forecast_net_sales=float(r.net_sales), target_splh=target,
                             required_hours=req, baseline_hours=base, delta_hours=delta, status=st))
            if st == "short" and delta >= 3:
                short.append((r.forecast_date, delta))
            if st == "over" and delta <= -3:
                over.append((r.forecast_date, -delta))
        for lst, kind in ((short, "short"), (over, "over")):
            if not lst:
                continue
            hrs = sum(h for _, h in lst)
            days = ", ".join(f"{d:%a %d}" for d, _ in lst[:5]) + ("…" if len(lst) > 5 else "")
            det = [dict(date=str(d.date()), hours=round(h, 1)) for d, h in lst]
            if kind == "short":
                impact = hrs * target * settings.UNDERSTAFF_LEAKAGE
                recs.append(m.Recommendation(
                    store_id=sid, kind="staffing", priority=1 if hrs >= 24 else 2,
                    title=f"Agregar {hrs:.0f} horas de personal ({len(lst)} días)",
                    summary=f"{stn.store_name[sid]}: ventas previstas superan la capacidad del turno habitual en {days}. "
                            f"Impacto = ventas en riesgo (supuesto: {int(settings.UNDERSTAFF_LEAKAGE * 100)} % de la demanda no atendida).",
                    est_impact=round(float(impact), 2),
                    payload=dict(direction="add", hours=round(hrs, 1), days=det, target_splh=round(target, 1))))
            else:
                impact = hrs * settings.WAGE_PER_HOUR
                recs.append(m.Recommendation(
                    store_id=sid, kind="staffing", priority=3,
                    title=f"Reducir {hrs:.0f} horas de personal ({len(lst)} días)",
                    summary=f"{stn.store_name[sid]}: la demanda prevista pide menos horas que las programadas en {days}. "
                            f"Ahorro calculado con USD {settings.WAGE_PER_HOUR:.2f}/h (supuesto).",
                    est_impact=round(float(impact), 2),
                    payload=dict(direction="trim", hours=round(hrs, 1), days=det, target_splh=round(target, 1))))
    db.bulk_insert_mappings(m.StaffingPlan, rows)
    db.add_all(recs)
    return len(recs)


# ------------------------------------------------------------------ analytics tables
def promo_performance(db):
    db.execute(delete(m.PromoPerformance))
    s = _q(db, """select s.date,s.store_id,s.units_sold,p.department,p.category,si.region
                  from sales_daily s join products p using(sku) join stores st using(store_id) join sites si using(site_id)
                  where s.dq_flag is null or s.dq_flag <> 'return'""")
    s["date"] = pd.to_datetime(s.date)
    first = s.date.min()
    for p in _q(db, "select * from promotions").itertuples():
        sc = s
        if isinstance(p.department, str):
            sc = sc[sc.department == p.department]
        if isinstance(p.category, str):
            sc = sc[sc.category == p.category]
        if isinstance(p.region_scope, str) and p.region_scope.upper() != "ALL":
            sc = sc[sc.region == p.region_scope]
        a, b = pd.to_datetime(p.start_date), pd.to_datetime(p.end_date)
        note, uplift, bu, pu = None, None, None, None
        if not bool(p.is_valid):
            note = "Fechas inconsistentes (fin antes del inicio): excluida"
        else:
            def per_day(x):
                return x.units_sold.sum() / max(x.groupby(["store_id", "date"]).ngroups, 1) if len(x) else None
            pu = per_day(sc[(sc.date >= a) & (sc.date <= b)])
            base_win = sc[(sc.date >= a - pd.Timedelta(days=28)) & (sc.date < a)]
            bu = per_day(base_win) if a - pd.Timedelta(days=28) >= first else None
            if bu and pu:
                uplift = (pu / bu - 1) * 100
            else:
                note = "Sin 28 días previos de datos para comparar" if bu is None else "Sin ventas en el alcance"
        db.add(m.PromoPerformance(promo_id=p.promo_id, baseline_units_per_store_day=bu, promo_units_per_store_day=pu,
                                  realized_uplift_pct=uplift, expected_uplift_pct=p.expected_uplift_pct,
                                  is_valid=bool(p.is_valid), note=note))


def supplier_performance(db):
    db.execute(delete(m.SupplierPerformance))
    po = _q(db, "select supplier_id,status,qty_ordered,qty_received,expected_date,received_date from purchase_orders")
    sp = _q(db, "select * from suppliers").set_index("supplier_id")
    done = po[po.status.isin(["Delivered", "Short-shipped"])].copy()
    done["late"] = pd.to_datetime(done.received_date) > pd.to_datetime(done.expected_date)
    done["has_dates"] = done.received_date.notna() & done.expected_date.notna()
    for sid, g in done.groupby("supplier_id"):
        ot = g[g.has_dates]
        db.add(m.SupplierPerformance(
            supplier_id=sid, po_lines=int(len(g)), fill_rate=float(g.qty_received.sum() / max(g.qty_ordered.sum(), 1)),
            on_time_rate=float(1 - ot.late.mean()) if len(ot) else None,
            contract_fill_rate=float(sp.contract_fill_rate[sid]), contract_on_time_rate=float(sp.contract_on_time_rate[sid])))
