"""Generate db/01_schema.sql (PostgreSQL DDL) from the SQLAlchemy models. Run from the repo root:
    python scripts/export_schema.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("DATABASE_URL", "sqlite://")
from sqlalchemy.dialects import postgresql                      # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable          # noqa: E402

from app import models                                          # noqa: E402,F401
from app.db import Base                                         # noqa: E402

HEADER = """-- RetailIQ / Glazed Co. - PostgreSQL 16 schema (Cloud SQL compatible).
-- GENERATED from backend/app/models.py by scripts/export_schema.py - edit the models, not this file.
-- Order: dimensions -> facts -> governance -> analytics -> decisions (tables are topologically sorted by FK).
-- docker compose mounts this folder into postgres:/docker-entrypoint-initdb.d, so a fresh database gets these tables.
"""
out = [HEADER]
d = postgresql.dialect()
for t in Base.metadata.sorted_tables:
    out.append(str(CreateTable(t).compile(dialect=d)).strip() + ";\n")
    for ix in sorted(t.indexes, key=lambda i: i.name or ""):
        out.append(str(CreateIndex(ix).compile(dialect=d)).strip() + ";")
    out.append("")
path = os.path.join(os.path.dirname(__file__), "..", "db", "01_schema.sql")
open(path, "w").write("\n".join(out).replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ").replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "))
print("written", os.path.abspath(path))
