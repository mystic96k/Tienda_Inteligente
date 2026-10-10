import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import threading

from sqlalchemy import func, select, text

from . import models as m
from .api import router
from .config import settings
from .data.loader import load_all
from .db import Base, SessionLocal, engine
from .ml import forecast as fc
from .services import engine as eng
from .state import status

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("retail")
_here = os.path.dirname(__file__)
REPORTING_SQL = next((p for p in (os.path.join(_here, "..", "db", "02_reporting_views.sql"),
                                  os.path.join(_here, "..", "..", "db", "02_reporting_views.sql")) if os.path.exists(p)), "")


def bootstrap():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        empty = not db.scalar(select(func.count(m.Site.site_id)))
        if empty and settings.AUTO_LOAD:
            status["stage"] = "descargando y limpiando los CSV, cargando la base de datos"
            log.info("loading dataset into an empty database")
            load_all()
        if db.scalar(select(func.count(m.Store.store_id))) and (fc.bundle() is None or not db.scalar(select(func.count(m.Recommendation.id)))):
            status["stage"] = "entrenando modelos de pronóstico"
            log.info("training models + generating recommendations")
            fc.train_all(db)
            status["stage"] = "generando recomendaciones"
            eng.run_all(db)
        if engine.dialect.name == "postgresql" and REPORTING_SQL:
            with engine.begin() as c:
                for stmt in open(REPORTING_SQL).read().split(";"):
                    if stmt.strip():
                        c.execute(text(stmt))
    finally:
        db.close()


def _run():
    try:
        bootstrap()
        status.update(ready=True, stage="listo")
    except Exception as e:  # keep the API up and report the reason through /api/health
        log.exception("bootstrap failed")
        status.update(error=str(e)[:300], stage="error")


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=_run, daemon=True).start()
    yield


app = FastAPI(title="RetailIQ - Glazed Co. operations", version="2.0", lifespan=lifespan)
app.include_router(router)

if settings.FRONTEND_DIR and os.path.isdir(settings.FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=settings.FRONTEND_DIR, html=True), name="web")
