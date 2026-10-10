from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_kwargs = {"pool_pre_ping": True}
if settings.DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


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
