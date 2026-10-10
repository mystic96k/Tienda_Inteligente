#!/usr/bin/env bash
# Provision + deploy on Google Cloud. Review variables, then run:  bash cloud/deploy_gcp.sh
set -euo pipefail
PROJECT=${PROJECT:?set PROJECT}; REGION=${REGION:-us-central1}; DB_PASS=${DB_PASS:?set DB_PASS}
INSTANCE=retail-pg; REPO=retailiq

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com bigquery.googleapis.com aiplatform.googleapis.com cloudscheduler.googleapis.com \
  pubsub.googleapis.com cloudbuild.googleapis.com

# --- OLTP database: Cloud SQL for PostgreSQL (HA, PITR, private IP recommended)
gcloud sql instances create $INSTANCE --database-version=POSTGRES_16 --region="$REGION" \
  --tier=db-custom-2-8192 --availability-type=REGIONAL --backup-start-time=03:00 --enable-point-in-time-recovery
gcloud sql databases create retail --instance=$INSTANCE
gcloud sql users create retail --instance=$INSTANCE --password="$DB_PASS"

# --- secrets
# Cloud Run does not expand $(VAR) inside env vars, so the full URL lives in one secret.
# If DB_PASS has special characters, URL-encode it first.
printf %s "postgresql+psycopg2://retail:${DB_PASS}@/retail?host=/cloudsql/$PROJECT:$REGION:$INSTANCE" | gcloud secrets create database-url --data-file=-

SA=$(gcloud iam service-accounts list --format='value(email)' --filter='displayName:Compute Engine default')
for ROLE in roles/secretmanager.secretAccessor roles/cloudsql.client; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role="$ROLE" --quiet >/dev/null
done
echo "paste GEMINI key:"; read -rs K; printf %s "$K" | gcloud secrets create gemini-api-key --data-file=-
openssl rand -hex 24 | tr -d '\n' | gcloud secrets create export-api-key --data-file=-

# --- build + deploy API (Cloud Run) and static dashboard (served by the API image via FRONTEND_DIR)
gcloud artifactregistry repositories create $REPO --repository-format=docker --location="$REGION" || true
IMG="$REGION-docker.pkg.dev/$PROJECT/$REPO/api:latest"
gcloud builds submit --tag "$IMG" .
gcloud run deploy retailiq --image "$IMG" --region "$REGION" --allow-unauthenticated=false \
  --add-cloudsql-instances "$PROJECT:$REGION:$INSTANCE" --cpu 2 --memory 4Gi --timeout 900 --min-instances 1 \
  --set-env-vars "AUTO_LOAD=true,AUTO_DOWNLOAD=true,DATA_DIR=/tmp/data,CURRENCY=USD" \
  --set-secrets "DATABASE_URL=database-url:latest,GEMINI_API_KEY=gemini-api-key:latest,EXPORT_API_KEY=export-api-key:latest"
# NOTE: put Identity-Aware Proxy / Cloud Run IAM in front for staff SSO; keep /api/export behind the API key.

gcloud run services add-iam-policy-binding retailiq --region "$REGION" --member="serviceAccount:$SA" --role=roles/run.invoker --quiet
# --- nightly retrain + recommendation refresh
gcloud scheduler jobs create http retailiq-nightly --schedule="0 3 * * *" --location="$REGION" \
  --uri="$(gcloud run services describe retailiq --region "$REGION" --format='value(status.url)')/api/engine/run" \
  --http-method=POST --oidc-service-account-email="$SA"

# --- analytics warehouse + ML (BigQuery)
bq --location=US mk -d retail_dw || true
echo "Next: run cloud/bigquery_ml.sql and set up Datastream / scheduled queries (see cloud/README.md)"
