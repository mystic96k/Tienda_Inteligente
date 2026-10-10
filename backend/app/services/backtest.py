"""Backtest: "replay the past" for the inventory policy.

Question answered: had the store used our ordering policy during the last 28 days of data, instead of the ordering it
actually did, how many sales would it have lost to stock-outs and how much stock would have expired?

How (no look-ahead):
  1. Train a copy of the SKU demand model ONLY on data up to the cut date (28 days before the end of the data).
  2. Forecasts for the window use features lagged >= 14 days, so they are legitimate 14-day-ahead forecasts.
  3. Ground truth = uncensored daily demand (units sold corrected for hours out of stock), the same target the model learns.
  4. Both policies are replayed in ONE simulator (FIFO stock, shelf life, same starting stock, same demand):
       * "actual"  - the real receipts that happened (what the stores really ordered)
       * "ours"    - orders placed by the same rule that powers the daily plan (plan.decide_order), subject to supplier
                     lead times and delivery weekdays
     Using one simulator for both removes simulator bias; only the ordering decisions differ.
  5. Calibration check: the simulator's result for the "actual" policy is compared with the stock-outs and expiries that
     really happened.
Deliveries: in the history, stores received goods on all seven weekdays even for suppliers whose calendar says Tue/Fri, so the
replay lets our policy order for any weekday (the live plan still honours the supplier calendar).
Caveats (shown in the UI): demand truth is an estimate; initial stock is assumed to be half-way through its shelf life; the
first lead-time days of arrivals are the real ones for both policies.
"""
import math
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import delete

from .. import models as m
from ..config import settings
from ..ml import forecast as fc
from . import engine as eng
from .plan import _arrival_weekdays, _gap_to_next, decide_order

WINDOW = 28
WARM = 7      # both policies receive the same (real) deliveries during the first week; metrics start after it


GROUPS: dict = {}
DEBUG: dict = {}   # (store_id, sku) -> list; filled for tracing a few pairs


def _sim(D, shelf, stock0, arr, price, cost, policy=None, log=None, start=0):
    """FIFO simulation of one store x SKU. Returns daily lost-margin, daily write-off cost, total sold, avg stock units."""
    W = len(D)
    batches = [[float(stock0), min(shelf / 2.0, shelf - 1)]] if stock0 > 0 else []
    margin = max(price - cost, 0.05)
    lost_usd, waste_usd = np.zeros(W), np.zeros(W)
    sold_tot, stock_sum, lost_u, exp_u = 0.0, 0.0, 0.0, 0.0
    arr = arr.copy()
    for t in range(W):
        if arr[t] > 0:
            batches.append([float(arr[t]), 0.0])
        keep = []
        for q, age in batches:
            if age >= shelf:
                if t >= start:
                    exp_u += q
                    waste_usd[t] += q * cost
            else:
                keep.append([q, age])
        batches = keep
        stock = sum(b[0] for b in batches)
        if policy is not None:
            policy(t, stock, arr)
        need = float(D[t])
        sold = min(stock, need)
        rem = sold
        for b in batches:
            take = min(b[0], rem)
            b[0] -= take
            rem -= take
            if rem <= 1e-9:
                break
        batches = [b for b in batches if b[0] > 1e-9]
        lost = need - sold
        for b in batches:
            b[1] += 1.0
        if t >= start:
            lost_u += lost
            lost_usd[t] += lost * margin
            sold_tot += sold
            stock_sum += sum(b[0] for b in batches)
        if log is not None:
            log.append((t, round(stock, 1), round(need, 1), round(sold, 1), round(float(arr[t]), 1)))
    return lost_usd, waste_usd, sold_tot, stock_sum / max(W - start, 1), lost_u, exp_u


def run(db) -> dict:
    df, last = fc._sku_panel(db)
    cut = last - pd.Timedelta(days=WINDOW)
    hist = df[df.demand.notna() & df.roll28.notna() & df.lag28.notna()]
    tr = hist[hist.date <= cut]
    mdl = fc._model(fc.SKU_CAT).fit(tr[fc.SKU_F], tr.demand)
    win = df[(df.date > cut) & (df.date <= last)].copy()
    win["pred"] = np.clip(mdl.predict(win[fc.SKU_F]), 0, None)
    dates = list(pd.date_range(cut + pd.Timedelta(days=1), last))
    piv = lambda col: win.pivot(index="sid", columns="date", values=col).reindex(columns=dates)   # noqa: E731
    P, Dm, Um = piv("pred"), piv("demand"), piv("units")
    keys = win[["sid", "store_id", "sku"]].drop_duplicates().set_index("sid")

    inv = eng._q(db, f"""select date, store_id, sku, received_qty, expired_qty, closing_qty, on_order_qty
                         from inventory_daily where date >= '{cut.date()}'""")
    inv["date"] = pd.to_datetime(inv.date)
    rec = inv[inv.date > cut].pivot_table(index=["store_id", "sku"], columns="date", values="received_qty", aggfunc="sum").reindex(columns=dates)
    exp_w = inv[inv.date > cut].pivot_table(index=["store_id", "sku"], columns="date", values="expired_qty", aggfunc="sum").reindex(columns=dates).fillna(0)
    st0 = inv[inv.date == cut].set_index(["store_id", "sku"]).closing_qty
    prod = eng._q(db, """select p.sku, p.unit_cost, p.list_price_base price, p.case_size, p.shelf_life_days shelf,
                         s.standard_lead_time_days lead, s.delivery_days from products p left join suppliers s using(supplier_id)""").set_index("sku")

    W = len(dates)
    tot = {k: np.zeros(W) for k in ("a_lost", "a_waste", "o_lost", "o_waste")}
    S = dict(a_sold=0.0, o_sold=0.0, a_stock=0.0, o_stock=0.0, a_lostu=0.0, o_lostu=0.0, a_expu=0.0, o_expu=0.0,
             real_lostu=0.0, real_expu=0.0, demand=0.0, a_stock_val=0.0, o_stock_val=0.0, pairs=0, real_lost_usd=0.0, real_waste_usd=0.0)
    by_store: dict = {}
    for sid, kk in keys.iterrows():
        d = Dm.loc[sid].to_numpy(dtype=float)
        if not np.isfinite(d).any() or kk.sku not in prod.index:
            continue
        d = np.nan_to_num(d, nan=0.0).clip(min=0)
        pr = prod.loc[kk.sku]
        cost, price = float(pr.unit_cost), float(pr.price)
        shelf = float(pr.shelf) if pd.notna(pr.shelf) else 30.0
        case = max(int(pr.case_size), 1) if pd.notna(pr.case_size) else 1
        L = max(1, int(round(pr.lead)) if pd.notna(pr.lead) else 2)
        allowed = set(range(7))   # the data shows deliveries on every weekday whatever the supplier calendar says -> same freedom as reality
        margin = max(price - cost, 0.05)
        key = (kk.store_id, kk.sku)
        stock0 = float(st0.get(key, 0.0)) if pd.notna(st0.get(key, 0.0)) else 0.0
        real_arr = np.nan_to_num(rec.loc[key].to_numpy(dtype=float)) if key in rec.index else np.zeros(W)
        f = np.nan_to_num(P.loc[sid].to_numpy(dtype=float), nan=0.0).clip(min=0)
        dbar, tail = max(float(f.mean()), 0.05), max(float(f[-7:].mean()), 0.05)
        cum = np.concatenate([[0.0], np.cumsum(np.concatenate([f, np.full(L + 10, tail)]))])
        sd = 1.3 * math.sqrt(dbar + 0.25)

        a = _sim(d, shelf, stock0, real_arr, price, cost, start=WARM)
        ours_arr = np.zeros(W + L + 2)
        ours_arr[:WARM] = real_arr[:WARM]                           # warm-up week: identical deliveries for both

        def policy(t, stock, arr, _a=allowed, _L=L, _cum=cum, _sd=sd, _shelf=shelf, _case=case, _m=margin, _c=cost):
            wd = (dates[t] + pd.Timedelta(days=_L)).dayofweek
            if wd in _a and t + _L >= WARM:
                dec = decide_order(t, stock, arr[t + 1:].sum(), _cum, _sd, _L, _gap_to_next(wd, _a), _shelf, _case, _m, 0.0, _c)
                if dec:
                    arr[t + _L] += dec[0]

        o = _sim(d, shelf, stock0, ours_arr, price, cost, policy=policy, log=DEBUG.get(key), start=WARM)
        tot["a_lost"] += a[0]; tot["a_waste"] += a[1]; tot["o_lost"] += o[0]; tot["o_waste"] += o[1]
        g = GROUPS.setdefault((str(pr.delivery_days), "<=3" if shelf <= 3 else "<=7" if shelf <= 7 else "<=30" if shelf <= 30 else ">30"), np.zeros(9))
        g += [1, a[1].sum(), o[1].sum(), a[0].sum(), o[0].sum(), a[5], o[5], d[WARM:].sum(), case]
        S["a_sold"] += a[2]; S["o_sold"] += o[2]; S["a_stock"] += a[3]; S["o_stock"] += o[3]
        S["a_lostu"] += a[4]; S["o_lostu"] += o[4]; S["a_expu"] += a[5]; S["o_expu"] += o[5]
        S["a_stock_val"] += a[3] * cost; S["o_stock_val"] += o[3] * cost
        u = np.nan_to_num(Um.loc[sid].to_numpy(dtype=float))
        dw, uw = d[WARM:], u[WARM:]
        S["real_lostu"] += float(max(dw.sum() - uw.sum(), 0)); S["demand"] += float(dw.sum())
        e = float(exp_w.loc[key].to_numpy(dtype=float)[WARM:].sum()) if key in exp_w.index else 0.0
        S["real_expu"] += e
        S["real_lost_usd"] += float(max(dw.sum() - uw.sum(), 0)) * margin; S["real_waste_usd"] += e * cost
        S["pairs"] += 1
        b = by_store.setdefault(kk.store_id, dict(a=0.0, o=0.0))
        b["a"] += a[0].sum() + a[1].sum(); b["o"] += o[0].sum() + o[1].sum()

    a_lost, a_waste, o_lost, o_waste = (float(tot[k].sum()) for k in ("a_lost", "a_waste", "o_lost", "o_waste"))
    dem = max(S["demand"], 1e-9)
    res = dict(
        window=dict(start=str(dates[WARM].date()), end=str(dates[-1].date()), days=W - WARM, pairs=S["pairs"], warmup_days=WARM),
        actual=dict(lost_margin_usd=round(a_lost), waste_usd=round(a_waste), fill_rate=round(S["a_sold"] / dem, 4),
                    lost_units=round(S["a_lostu"]), expired_units=round(S["a_expu"]), avg_stock_value_usd=round(S["a_stock_val"])),
        ours=dict(lost_margin_usd=round(o_lost), waste_usd=round(o_waste), fill_rate=round(S["o_sold"] / dem, 4),
                  lost_units=round(S["o_lostu"]), expired_units=round(S["o_expu"]), avg_stock_value_usd=round(S["o_stock_val"])),
        observed=dict(lost_units=round(S["real_lostu"]), expired_units=round(S["real_expu"]),
                      lost_margin_usd=round(S["real_lost_usd"]), waste_usd=round(S["real_waste_usd"])),
        saved=dict(lost_margin_usd=round(a_lost - o_lost), waste_usd=round(a_waste - o_waste), net_usd=round((a_lost + a_waste) - (o_lost + o_waste)),
                   stock_change_pct=round(100 * (S["o_stock_val"] / max(S["a_stock_val"], 1e-9) - 1), 1)),
        daily=dict(dates=[str(x.date()) for x in dates[WARM:]], actual=[round(float(v)) for v in np.cumsum((tot["a_lost"] + tot["a_waste"])[WARM:])],
                   ours=[round(float(v)) for v in np.cumsum((tot["o_lost"] + tot["o_waste"])[WARM:])]),
        by_store=sorted([dict(store_id=k, actual=round(v["a"]), ours=round(v["o"]), saved=round(v["a"] - v["o"])) for k, v in by_store.items()],
                        key=lambda x: -x["saved"]),
        forecast=dict(wape_model=None),
    )
    hv = hist[hist.date > cut]
    pv = np.clip(mdl.predict(hv[fc.SKU_F]), 0, None)
    res["forecast"] = dict(wape_model=round(fc._wape(hv.demand.values, pv), 4), wape_naive=round(fc._wape(hv.demand.values, hv.roll28.values), 4))
    db.execute(delete(m.BacktestResult))
    db.add(m.BacktestResult(window_start=dates[0].date(), window_end=dates[-1].date(), results=res, created_at=datetime.utcnow()))
    db.commit()
    return res
