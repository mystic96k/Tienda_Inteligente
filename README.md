# RetailIQ · Smart Retail Store Solution

Recomendaciones por tienda sobre **promociones, inventario y personal**, con ML, Gemini, tablero Liquid Glass y Power BI.
Datos: [dataset del hackathon Road2Tech](https://github.com/ams-innovation/dataset-hacktahton-road2tech) (sintético, solo uso del evento: **no se sube al repo**; se descarga solo).

## Qué muestra el tablero
Arriba, **Plan del día**: eliges tienda y fecha y ves qué reponer (con fecha de llegada del proveedor), qué promocionar (con fechas) y cuánto personal asignar. Sin tienda seleccionada muestra el ranking de tiendas para ese día.

## Estructura
```
backend/app/        API FastAPI, config, modelos SQLAlchemy
  data/loader.py    descarga + limpieza + carga
  ml/forecast.py    modelos de tienda y SKU
  services/         engine.py (decisiones), gemini.py
backend/tests/      pruebas
db/                 01_schema.sql (tablas), 02_reporting_views.sql (vistas Power BI)
frontend/           index.html, styles.css, app.js, nginx.conf, Dockerfile
scripts/            export_schema.py, fetch_dataset.py
data/               CSV locales (ignorados por git)
cloud/              deploy_gcp.sh, bigquery_ml.sql, README
powerbi/            guía Power BI
docs/ARCHITECTURE.md
.github/workflows/  deploy.yml
Dockerfile, docker-compose.yml, .env.example
```

## Correr con Docker (Mac y Windows)
```
cp .env.example .env        # Windows: copy .env.example .env
docker compose up --build
```
Tablero: http://localhost:8080 · API: http://localhost:8000/docs.
La primera vez descarga ~80 MB, limpia, entrena y genera recomendaciones (varios minutos; el tablero muestra el avance). Dale a Docker ≥ 4 GB de RAM.

## Sin Docker (VS Code)
```
python -m venv .venv
.venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements-dev.txt
cd backend
DATABASE_URL=sqlite:///./retail.db FRONTEND_DIR=../frontend uvicorn app.main:app --reload
```
(Windows PowerShell: `$env:DATABASE_URL="sqlite:///./retail.db"; $env:FRONTEND_DIR="../frontend"`.) Pruebas: `pytest`.

## Gemini
Pon `GEMINI_API_KEY` en `.env` (nunca lo subas). Sin llave hay resúmenes de respaldo. Verifica que `GEMINI_MODEL` exista en tu cuenta.

## Despliegue
GitHub → Actions → Cloud Run + Cloud SQL: ver `cloud/README.md`.

## Despliegue web
Ver [docs/DEPLOY.md](docs/DEPLOY.md): túnel rápido, VM con HTTPS (`docker-compose.prod.yml`) o Cloud Run. Para cualquier URL pública define `ADMIN_KEY`.
