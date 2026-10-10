# Cloud reference setup (Google Cloud)

| Concern | Service | Why |
|---|---|---|
| Transactional DB | **Cloud SQL for PostgreSQL** (HA, PITR, private IP) | Same SQLAlchemy models as local Docker – only `DATABASE_URL` changes |
| API + dashboard | **Cloud Run** (container built from `Dockerfile`) | Autoscaling, scale-to-zero optional, IAM / IAP |
| Analytics + ML at scale | **BigQuery + BQML** (`bigquery_ml.sql`), **Vertex AI** for custom models/AutoML | Years of history, 100k+ series, SQL-native training |
| Ingest POS events | **Pub/Sub → Dataflow → BigQuery / Cloud SQL** | Near-real-time sales & stock updates |
| Sync OLTP → DW | **Datastream** (CDC) | No custom ETL |
| Scheduling | **Cloud Scheduler** → `POST /api/engine/run` | Nightly retrain + recommendations |
| LLM | **Gemini API** (AI Studio key) or **Vertex AI Gemini** (IAM auth, VPC-SC, data residency) | Switch by replacing the REST URL/auth in `services/gemini.py` |
| Secrets | **Secret Manager** | DB password, Gemini key, export key |
| BI | **Power BI** → Cloud SQL `rpt` schema / BigQuery connector | See `powerbi/` |

Deploy: `PROJECT=my-proj DB_PASS=… bash cloud/deploy_gcp.sh` (read it first; it creates billable resources).
Run the same image locally against Cloud SQL with `docker compose --profile cloud up`.

Production hardening checklist: private IP + no public DB, IAP/SSO in front of the dashboard, per-branch row-level
security (branch manager sees only their branch), read-only `powerbi` role, CMEK, audit logs, Cloud Armor, `AUTO_LOAD=true` (carga inicial; luego puedes ponerlo en false),
Alembic migrations instead of `create_all`, model registry (Vertex) with shadow evaluation before promotion.
