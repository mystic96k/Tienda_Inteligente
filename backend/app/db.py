from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_kwargs = {"pool_pre_ping": True}
if settings.DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


# PostgreSQL (psycopg2) cannot adapt numpy scalars ("schema np does not exist"); SQLite can. Register adapters once.
try:
    import math

    import numpy as np
    from psycopg2.extensions import AsIs, register_adapter

    def _num(x):
        v = float(x)
        return AsIs("NULL" if math.isnan(v) or math.isinf(v) else repr(v))

    for _t in (np.float64, np.float32, np.float16):
        register_adapter(_t, _num)
    for _t in (np.int64, np.int32, np.int16, np.int8, np.uint64, np.uint32, np.uint16, np.uint8):
        register_adapter(_t, lambda x: AsIs(repr(int(x))))
    register_adapter(np.bool_, lambda x: AsIs("true" if bool(x) else "false"))
except ImportError:  # SQLite-only environments
    pass


def get_db():
    from fastapi import HTTPException
    from .state import status
    if not status["ready"]:
        raise HTTPException(503, detail={"loading": True, "stage": status["stage"], "error": status["error"]})
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
