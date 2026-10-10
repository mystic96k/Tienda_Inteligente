"""End-to-end test on a 4-store slice of the hackathon dataset (downloaded on first run unless DATA_DIR is set)."""
import os
import tempfile

_t = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{_t}/t.db", MODEL_DIR=f"{_t}/m", GEMINI_API_KEY="", EXPORT_API_KEY="k",
                  DEMO_STORES="4", DATA_DIR=os.environ.get("DATA_DIR", f"{_t}/data"), AUTO_DOWNLOAD="true")
from fastapi.testclient import TestClient  # noqa: E402

import time  # noqa: E402

from app.main import app  # noqa: E402


def test_end_to_end():
    with TestClient(app) as c:
        for _ in range(240):                                   # start-up runs in the background
            h = c.get("/api/health").json()
            if h["ready"] or h["error"]:
                break
            time.sleep(2)
        assert h["ready"], h
        m = c.get("/api/model").json()["metrics"]
        assert m["store"]["wape_model"] < 1.25 * m["store"]["wape_naive"]      # sane on a 4-store slice (full data: see docs)
        stores = c.get("/api/stores").json()
        assert len(stores) == 4 and all(s["store_id"].startswith("S") and len(s["store_id"]) == 4 for s in stores)
        issues = {d["issue"] for d in c.get("/api/data-quality").json()}
        assert any("duplicate" in i for i in issues) and any("SKU written" in i for i in issues)
        kinds = {r["kind"] for r in c.get("/api/recommendations?limit=500").json()}
        assert {"inventory", "staffing"} <= kinds
        k = c.get("/api/kpis").json()
        assert k["net_sales_30d"] > 0 and k["forecast_14d"] > 0
        assert len(c.get("/api/staffing").json()) >= 14
        promos = {p["promo_id"]: p for p in c.get("/api/promotions/performance").json()}
        assert not promos["PR003"]["is_valid"]                           # inconsistent dates detected
        assert c.get("/api/export/stores.csv").status_code == 401
        assert c.get("/api/export/stores.csv", headers={"X-API-Key": "k"}).status_code == 200
        assert "headline" in c.post("/api/ai/brief", json={}).json()           # offline fallback
        r = c.get("/api/recommendations?limit=1").json()[0]
        assert c.post(f"/api/recommendations/{r['id']}/approve").json()["status"] == "approved"
