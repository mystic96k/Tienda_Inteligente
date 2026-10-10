import csv
import io
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import models as m
from .config import settings
from .db import get_db
from .services import engine as eng
from .services import gemini

router = APIRouter(prefix="/api")


def usd(x):
    return f"USD {x:,.0f}"


def _df(db, sql, **p):
    return pd.read_sql(text(sql), db.bind, params=p)


def _one(db, sql, **p):
    return db.execute(text(sql), p).one()


# ------------------------------------------------------------------ KPIs
def kpis(db: Session, store_id: Optional[str]) -> dict:
    last = pd.to_datetime(_one(db, "select max(date) from store_daily_kpis")[0]).date()
    d30, d60 = last - timedelta(days=29), last - timedelta(days=59)
    sf = " and store_id = :sid" if store_id else ""
    p = {"sid": store_id} if store_id else {}
    cur = _one(db, f"select coalesce(sum(net_sales),0), coalesce(sum(units_sold),0) from store_daily_kpis where date between :a and :b{sf}", a=d30, b=last, **p)
    prev = _one(db, f"select coalesce(sum(net_sales),0) from store_daily_kpis where date between :a and :b{sf}", a=d60, b=d30 - timedelta(days=1), **p)
    waste = _one(db, f"""select coalesce(sum(i.expired_qty * p.unit_cost),0), coalesce(sum(i.stockout_hours),0),
                                coalesce(sum(i.units_sold * p.unit_cost),0)
                         from inventory_daily i join products p using(sku) where i.date between :a and :b{sf.replace('store_id', 'i.store_id')}""",
                 a=d30, b=last, **p)
    fcast = _one(db, f"select coalesce(sum(net_sales),0) from store_forecast where 1=1{sf}", **p)[0]
    st = _one(db, f"""select coalesce(sum(case when status='short' then delta_hours else 0 end),0),
                             coalesce(sum(case when status='over' then -delta_hours else 0 end),0)
                      from staffing_plan where 1=1{sf}""", **p)
    rc = _one(db, f"""select count(*), coalesce(sum(est_impact),0),
                             coalesce(sum(case when kind='inventory' then 1 else 0 end),0)
                      from recommendations where status='open'{sf}""", **p)
    rev, prev_rev = float(cur[0]), float(prev[0])
    return dict(as_of=str(last), net_sales_30d=rev, net_sales_30d_fmt=usd(rev),
                change_pct=round(100 * (rev / prev_rev - 1), 1) if prev_rev else 0.0, units_30d=int(cur[1]),
                waste_usd_30d=float(waste[0]), waste_usd_30d_fmt=usd(waste[0]), stockout_hours_30d=float(waste[1]),
                forecast_14d=float(fcast), forecast_14d_fmt=usd(fcast),
                short_hours=round(float(st[0])), over_hours=round(float(st[1])),
                open_recommendations=int(rc[0]), potential_impact=float(rc[1]), potential_impact_fmt=usd(rc[1]))


def rec_dict(r: m.Recommendation, name=None):
    return dict(id=r.id, store_id=r.store_id, store=name, kind=r.kind, priority=r.priority, title=r.title, summary=r.summary,
                est_impact=r.est_impact, payload=r.payload, status=r.status)


def context_for(db, store_id):
    names = dict(db.execute(text("select store_id, store_name from stores")).all())
    q = select(m.Recommendation).where(m.Recommendation.status == "open").order_by(
        m.Recommendation.priority, m.Recommendation.est_impact.desc()).limit(12)
    if store_id:
        q = q.where(m.Recommendation.store_id == store_id)
    run = db.scalars(select(m.ModelRun).order_by(m.ModelRun.id.desc())).first()
    promos = _df(db, "select p.promo_name, pp.realized_uplift_pct, pp.expected_uplift_pct, pp.note from promo_performance pp join promotions p using(promo_id)")
    wx = _df(db, """select w.site_id, w.date, w.temp_max_c, w.temp_min_c, w.precip_mm from weather_daily w
                    where (:sid is null or w.site_id = (select site_id from stores where store_id = :sid))""", sid=store_id)
    if len(wx):
        wx["date"] = pd.to_datetime(wx.date)
        wx = wx[wx.date > wx.date.max() - pd.Timedelta(days=14)].groupby("date")[["temp_max_c", "temp_min_c", "precip_mm"]].mean().round(1)
        wx.insert(0, "date", wx.index.strftime("%Y-%m-%d"))
    return dict(scope=names.get(store_id, "Toda la red"), kpis=kpis(db, store_id),
                top_recommendations=[rec_dict(r, names[r.store_id]) for r in db.scalars(q)],
                promo_scorecard=promos.to_dict("records"), model=run.metrics if run else None,
                weather_last_14_days=wx.to_dict("records"))


# ------------------------------------------------------------------ endpoints
def _admin(x_admin_key: Optional[str] = Header(None)):
    """Write actions (recalculate, approve/dismiss, AI copy) need the admin key when ADMIN_KEY is set."""
    if settings.ADMIN_KEY and x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(401, "admin key required")


@router.get("/health")
def health():
    from .state import status
    return dict(status="ok" if status["ready"] else "loading", ready=status["ready"], stage=status["stage"],
                error=status["error"], gemini=gemini.available())


@router.get("/config")
def config():
    return dict(currency=settings.CURRENCY, gemini=gemini.available(), powerbi_embed_url=settings.POWERBI_EMBED_URL,
                export_auth=bool(settings.EXPORT_API_KEY), wage=settings.WAGE_PER_HOUR, admin_required=bool(settings.ADMIN_KEY),
                assumptions=dict(wage_per_hour=settings.WAGE_PER_HOUR, service_z=settings.SERVICE_Z, understaff_leakage=settings.UNDERSTAFF_LEAKAGE,
                                 carry_cost_per_day=settings.CARRY_COST_PER_DAY, staff_tolerance=settings.STAFF_TOLERANCE,
                                 splh_percentile=settings.SPLH_PERCENTILE, min_staff_ratio=settings.MIN_STAFF_RATIO))


@router.get("/stores")
def stores(db: Session = Depends(get_db)):
    base = _df(db, """select s.store_id, s.store_name, s.format, s.closed_date, si.site_name, si.site_type, si.region
                      from stores s join sites si using(site_id) order by s.store_id""")
    k = _df(db, "select max(date) d from store_daily_kpis").d[0]
    last = pd.to_datetime(k).date()
    a, b = last - timedelta(days=29), last
    cur = _df(db, "select store_id, sum(net_sales) rev from store_daily_kpis where date between :a and :b group by store_id", a=a, b=b)
    prev = _df(db, "select store_id, sum(net_sales) prev from store_daily_kpis where date between :a and :b group by store_id",
               a=a - timedelta(days=30), b=a - timedelta(days=1))
    waste = _df(db, """select i.store_id, sum(i.expired_qty*p.unit_cost) waste, sum(i.stockout_hours) so_h from inventory_daily i
                       join products p using(sku) where i.date between :a and :b group by i.store_id""", a=a, b=b)
    rec = _df(db, "select store_id, count(*) n, sum(est_impact) impact from recommendations where status='open' group by store_id")
    out = base.merge(cur, how="left", on="store_id").merge(prev, how="left", on="store_id").merge(waste, how="left", on="store_id") \
        .merge(rec, how="left", on="store_id").fillna({"rev": 0, "prev": 0, "waste": 0, "so_h": 0, "n": 0, "impact": 0})
    out["change_pct"] = ((out.rev / out.prev.where(out.prev > 0) - 1) * 100).round(1).fillna(0)
    out["active"] = out.closed_date.isna()
    return out.drop(columns=["closed_date", "prev"]).to_dict("records")


@router.get("/kpis")
def kpis_ep(store_id: Optional[str] = None, db: Session = Depends(get_db)):
    return kpis(db, store_id)


@router.get("/sales/trend")
def trend(store_id: Optional[str] = None, days: int = 75, db: Session = Depends(get_db)):
    sf = " and store_id = :sid" if store_id else ""
    p = {"sid": store_id} if store_id else {}
    last = pd.to_datetime(_one(db, "select max(date) from store_daily_kpis")[0]).date()
    a = _df(db, f"select date, sum(net_sales) v from store_daily_kpis where date > :a{sf} group by date order by date", a=last - timedelta(days=days), **p)
    f = _df(db, f"select forecast_date d, sum(net_sales) v, sum(lower) lo, sum(upper) hi from store_forecast where 1=1{sf} group by forecast_date order by forecast_date", **p)
    return dict(actual=[dict(date=str(r.date), v=round(r.v)) for r in a.itertuples()],
                forecast=[dict(date=str(r.d), v=round(r.v), lo=round(r.lo), hi=round(r.hi)) for r in f.itertuples()])


@router.get("/recommendations")
def recommendations(store_id: Optional[str] = None, kind: Optional[str] = None, status: str = "open", limit: int = 40,
                    db: Session = Depends(get_db)):
    names = dict(db.execute(text("select store_id, store_name from stores")).all())
    q = select(m.Recommendation).where(m.Recommendation.status == status).order_by(
        m.Recommendation.priority, m.Recommendation.est_impact.desc()).limit(limit)
    if store_id:
        q = q.where(m.Recommendation.store_id == store_id)
    if kind:
        q = q.where(m.Recommendation.kind == kind)
    return [rec_dict(r, names[r.store_id]) for r in db.scalars(q)]


@router.post("/recommendations/{rid}/{action}", dependencies=[Depends(_admin)])
def act(rid: int, action: str, db: Session = Depends(get_db)):
    r = db.get(m.Recommendation, rid)
    if not r or action not in ("approve", "dismiss"):
        raise HTTPException(404, "not found")
    r.status = "approved" if action == "approve" else "dismissed"
    r.decided_at = datetime.utcnow()
    db.commit()
    return dict(id=rid, status=r.status)


@router.get("/staffing")
def staffing(store_id: Optional[str] = None, db: Session = Depends(get_db)):
    sf = " where store_id = :sid" if store_id else ""
    df = _df(db, f"select store_id, plan_date, forecast_net_sales, required_hours, baseline_hours, delta_hours, status from staffing_plan{sf} order by store_id, plan_date",
             **({"sid": store_id} if store_id else {}))
    return [dict(store_id=r.store_id, date=str(r.plan_date), sales=round(r.forecast_net_sales), required=round(r.required_hours, 1),
                 baseline=round(r.baseline_hours, 1), delta=round(r.delta_hours, 1), status=r.status) for r in df.itertuples()]


@router.get("/plan")
def daily_plan(store_id: str, db: Session = Depends(get_db)):
    """Everything to do in one store, day by day: reorders, promotions, staffing."""
    name = db.execute(text("select store_name from stores where store_id = :s"), {"s": store_id}).scalar()
    if name is None:
        raise HTTPException(404, "unknown store")
    status = dict(db.execute(text("select id, status from recommendations")).all())
    days: dict = {}
    for r in db.scalars(select(m.DailyPlan).where(m.DailyPlan.store_id == store_id).order_by(
            m.DailyPlan.plan_date, m.DailyPlan.priority, m.DailyPlan.est_impact.desc())):
        st = status.get(r.rec_id) if r.rec_id else None
        if st == "dismissed":
            continue
        d = days.setdefault(str(r.plan_date), dict(date=str(r.plan_date), orders=[], promos=[], staff=None))
        item = dict(id=r.id, kind=r.kind, sku=r.sku, title=r.title, detail=r.detail, qty=r.qty, est_impact=r.est_impact,
                    priority=r.priority, rec_id=r.rec_id, rec_status=st, **(r.payload or {}))
        if r.kind == "order":
            d["orders"].append(item)
        elif r.kind == "promo":
            d["promos"].append(item)
        else:
            d["staff"] = item
    out = []
    for d in sorted(days.values(), key=lambda x: x["date"]):
        d["impact"] = round(sum(i["est_impact"] or 0 for i in d["orders"] + d["promos"]) + (d["staff"]["est_impact"] if d["staff"] else 0), 2)
        out.append(d)
    return dict(store_id=store_id, store=name, days=out)


@router.get("/plan/orders.csv")
def plan_orders_csv(store_id: str, date: Optional[str] = None, db: Session = Depends(get_db)):
    """Purchase-order lines for one store (one day, or the whole horizon), ready to send to suppliers."""
    q = select(m.DailyPlan).where(m.DailyPlan.store_id == store_id, m.DailyPlan.kind == "order").order_by(m.DailyPlan.plan_date, m.DailyPlan.sku)
    status = dict(db.execute(text("select id, status from recommendations")).all())
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["store_id", "order_date", "arrival_date", "supplier", "sku", "product", "action", "units", "cases", "case_size"])
    for r in db.scalars(q):
        if date and str(r.plan_date) != date or status.get(r.rec_id) == "dismissed":
            continue
        p = r.payload or {}
        if p.get("action") != "reorder":
            continue
        w.writerow([r.store_id, r.plan_date, p.get("arrives"), p.get("supplier"), r.sku, p.get("product"), "order",
                    p.get("order_qty"), p.get("cases"), p.get("case_size")])
    name = f"pedidos_{store_id}{'_' + date if date else ''}.csv"
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/plan/network")
def daily_plan_network(db: Session = Depends(get_db)):
    """One line per store and day (counts + staffing balance) for the network view."""
    df = _df(db, """select p.store_id, s.store_name, p.plan_date, p.kind, p.est_impact, p.rec_id
                    from daily_plan p join stores s using(store_id)""")
    st = _df(db, "select store_id, plan_date, status, delta_hours from staffing_plan")
    status = dict(db.execute(text("select id, status from recommendations")).all())
    cells: dict = {}
    for r in df.itertuples():
        if pd.notna(r.rec_id) and status.get(int(r.rec_id)) == "dismissed":
            continue
        key = (r.store_id, str(r.plan_date)[:10])
        c = cells.setdefault(key, dict(store_id=r.store_id, store=r.store_name, date=key[1], orders=0, promos=0, status=None, delta=0.0, impact=0.0))
        c["impact"] += float(r.est_impact or 0)
        if r.kind == "order":
            c["orders"] += 1
        elif r.kind == "promo":
            c["promos"] += 1
    for r in st.itertuples():
        c = cells.get((r.store_id, str(r.plan_date)[:10]))
        if c:
            c["status"], c["delta"] = r.status, float(r.delta_hours)
    dates = sorted({k[1] for k in cells})
    return dict(dates=dates, cells=[{**c, "impact": round(c["impact"], 2), "delta": round(c["delta"], 1)} for c in cells.values()])


@router.get("/backtest")
def backtest_latest(db: Session = Depends(get_db)):
    r = db.scalars(select(m.BacktestResult).order_by(m.BacktestResult.id.desc())).first()
    if not r:
        return {"status": "pending"}
    return {"status": "ready", "created_at": r.created_at.isoformat(), **r.results}


@router.post("/backtest/run", dependencies=[Depends(_admin)])
def backtest_run(db: Session = Depends(get_db)):
    from .services import backtest
    return {"status": "ready", **backtest.run(db)}


@router.get("/promotions/performance")
def promo_perf(db: Session = Depends(get_db)):
    df = _df(db, """select p.promo_id, p.promo_name, p.start_date, p.end_date, p.discount_pct, p.department, p.category,
                    pp.realized_uplift_pct, pp.expected_uplift_pct, pp.is_valid, pp.note
                    from promo_performance pp join promotions p using(promo_id) order by p.start_date""")
    return df.astype(object).where(df.notna(), None).to_dict("records")


@router.get("/suppliers/performance")
def sup_perf(db: Session = Depends(get_db)):
    df = _df(db, """select s.supplier_id, s.supplier_name, sp.po_lines, sp.fill_rate, sp.on_time_rate, sp.contract_fill_rate, sp.contract_on_time_rate
                    from supplier_performance sp join suppliers s using(supplier_id) order by s.supplier_id""")
    return df.astype(object).where(df.notna(), None).to_dict("records")


@router.get("/data-quality")
def dq(db: Session = Depends(get_db)):
    return [dict(table=r.table_name, issue=r.issue, rows=r.rows_affected, treatment=r.treatment)
            for r in db.scalars(select(m.DqIssue).order_by(m.DqIssue.rows_affected.desc()))]


@router.post("/engine/run", dependencies=[Depends(_admin)])
def run_engine(db: Session = Depends(get_db)):
    from .ml import forecast as fc
    metrics = fc.train_all(db)
    return dict(model=metrics, recommendations=eng.run_all(db))


@router.get("/model")
def model_info(db: Session = Depends(get_db)):
    r = db.scalars(select(m.ModelRun).order_by(m.ModelRun.id.desc())).first()
    return dict(version=r.version, trained_at=r.trained_at, metrics=r.metrics) if r else {}


# ------------------------------------------------------------------ Gemini
class BriefReq(BaseModel):
    store_id: Optional[str] = None


class AskReq(BriefReq):
    question: str


@router.post("/ai/brief")
def ai_brief(req: BriefReq, db: Session = Depends(get_db)):
    return gemini.brief(context_for(db, req.store_id))


@router.post("/ai/ask")
def ai_ask(req: AskReq, db: Session = Depends(get_db)):
    return gemini.ask(req.question, context_for(db, req.store_id))


@router.post("/ai/promo-copy/{rid}", dependencies=[Depends(_admin)])
def ai_copy(rid: int, db: Session = Depends(get_db)):
    r = db.get(m.Recommendation, rid)
    if not r or r.kind != "promotion":
        raise HTTPException(404, "promotion recommendation not found")
    return gemini.promo_copy(rec_dict(r))


# ------------------------------------------------------------------ Power BI / BI export (small tables; use rpt views for facts)
def _auth(x_api_key: Optional[str] = Header(None), key: Optional[str] = Query(None)):
    if settings.EXPORT_API_KEY and (x_api_key or key) != settings.EXPORT_API_KEY:
        raise HTTPException(401, "invalid API key")


DATASETS = {
    "stores": "select s.store_id, s.store_name, s.format, s.operator_type, si.site_name, si.site_type, si.region, si.traffic_tier, s.opened_date, s.closed_date from stores s join sites si using(site_id)",
    "store_daily_kpis": "select * from store_daily_kpis",
    "store_forecast": "select * from store_forecast",
    "demand_forecast": "select * from demand_forecast",
    "staffing_plan": "select * from staffing_plan",
    "daily_plan": "select id, store_id, plan_date, kind, sku, title, detail, qty, est_impact, priority, rec_id from daily_plan",
    "recommendations": "select id, store_id, kind, priority, title, summary, est_impact, status, created_at from recommendations",
    "promo_performance": "select * from promo_performance",
    "supplier_performance": "select * from supplier_performance",
    "dq_issues": "select table_name, issue, rows_affected, treatment from dq_issues",
}


@router.get("/export/{dataset}.{fmt_}", dependencies=[Depends(_auth)])
def export(dataset: str, fmt_: str, db: Session = Depends(get_db)):
    if dataset not in DATASETS or fmt_ not in ("csv", "json"):
        raise HTTPException(404, "unknown dataset/format")
    res = db.execute(text(DATASETS[dataset]))
    cols, rows = list(res.keys()), [list(r) for r in res]
    if fmt_ == "json":
        return JSONResponse([dict(zip(cols, r)) for r in rows], headers={"Cache-Control": "no-store"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv")
