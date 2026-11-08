#!/usr/bin/env bash
# Explicit opt-in only: creates billable resources and optionally a public URL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT=""
REGION="us-central1"
MODE="dry-run"
ACCESS="private"
SERVICE="wealth-research-demo"
JOB="wealth-research-index"
INSTANCE="wealth-research-db"
DATABASE="research"
ADMIN_SECRET="wealth-copilot-admin-dsn"
WEB_SECRET="wealth-copilot-reader-dsn"

usage() {
  cat <<'EOF'
Usage: scripts/deploy_gcp.sh --project PROJECT [--region REGION] [--dry-run | --apply] [--public]

Dry-run (default): prints the plan; does not contact GCP.
Apply: enables APIs, creates billable Cloud SQL + secrets + service accounts,
builds and deploys the app and indexing job, runs indexing, then prints the URL.
--public makes the unauthenticated app available to anyone; use demo data only.

Before --apply, set CONFIRM_PROJECT=PROJECT. For --public also set
ALLOW_PUBLIC_DEMO=I_ACCEPT_PUBLIC_COSTS.
EOF
}

while (($#)); do
  case "$1" in
    --project) PROJECT="${2:?Missing project}"; shift 2 ;;
    --region) REGION="${2:?Missing region}"; shift 2 ;;
    --dry-run) MODE="dry-run"; shift ;;
    --apply) MODE="apply"; shift ;;
    --public) ACCESS="public"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! "$PROJECT" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  echo "Pass a valid GCP project ID via --project." >&2
  exit 2
fi
if [[ ! "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]]; then
  echo "Pass a valid GCP region via --region." >&2
  exit 2
fi

cat <<EOF
Project: $PROJECT                 Region: $REGION
Access:  $ACCESS (no application authentication)
Resources: Cloud SQL $INSTANCE (POSTGRES_16, db-f1-micro, 10GB),
           Cloud Run $SERVICE (max 1 instance), one-off job $JOB,
           2 service accounts, 2 Secret Manager DSNs, source build.
Model: Gemini 3.5 Flash (global) + text-embedding-005 (regional) on Vertex AI.
This incurs Cloud SQL, build, Vertex AI, and related charges until removed.
EOF
if [[ "$MODE" == "dry-run" ]]; then
  echo "Dry-run only. No GCP calls made and no URL exists yet."
  exit 0
fi

if [[ "${CONFIRM_PROJECT:-}" != "$PROJECT" ]]; then
  echo "Refusing deployment: set CONFIRM_PROJECT=$PROJECT." >&2
  exit 2
fi
if [[ "$ACCESS" == "public" && "${ALLOW_PUBLIC_DEMO:-}" != "I_ACCEPT_PUBLIC_COSTS" ]]; then
  echo "Refusing public deployment: set ALLOW_PUBLIC_DEMO=I_ACCEPT_PUBLIC_COSTS." >&2
  exit 2
fi
for executable in gcloud openssl curl python3; do
  command -v "$executable" >/dev/null || { echo "Missing $executable" >&2; exit 2; }
done

gc() { gcloud --project="$PROJECT" "$@"; }
CONNECTION="$PROJECT:$REGION:$INSTANCE"
WEB_ACCOUNT="wealth-copilot-web@$PROJECT.iam.gserviceaccount.com"
JOB_ACCOUNT="wealth-copilot-indexer@$PROJECT.iam.gserviceaccount.com"

echo "Enabling required APIs..."
gc services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com sqladmin.googleapis.com \
  secretmanager.googleapis.com aiplatform.googleapis.com --quiet

project_number="$(gc projects describe "$PROJECT" --format='value(projectNumber)')"
build_account="$project_number-compute@developer.gserviceaccount.com"
gc projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$build_account" --role=roles/run.builder \
  --quiet >/dev/null

for identity in wealth-copilot-web wealth-copilot-indexer; do
  if ! gc iam service-accounts describe "$identity@$PROJECT.iam.gserviceaccount.com" \
    >/dev/null 2>&1; then
    gc iam service-accounts create "$identity" \
      --display-name="Wealth Copilot ${identity##*-}" --quiet
  fi
  for role in roles/cloudsql.client roles/aiplatform.user; do
    bound=false
    for attempt in 1 2 3 4 5 6; do
      if binding_output="$(gc projects add-iam-policy-binding "$PROJECT" \
        --member="serviceAccount:$identity@$PROJECT.iam.gserviceaccount.com" \
        --role="$role" --quiet 2>&1)"; then
        bound=true
        break
      fi
      if [[ "$binding_output" != *"does not exist"* || "$attempt" == 6 ]]; then
        echo "$binding_output" >&2
        exit 1
      fi
      echo "Waiting for IAM service-account propagation ($attempt/6)..."
      sleep 10
    done
    [[ "$bound" == true ]] || exit 1
  done
done

instance_exists=false
if gc sql instances describe "$INSTANCE" >/dev/null 2>&1; then
  instance_exists=true
fi
if [[ "$instance_exists" == false ]]; then
  echo "Creating billable Cloud SQL PostgreSQL instance..."
  gc sql instances create "$INSTANCE" --region="$REGION" \
    --database-version=POSTGRES_16 --edition=ENTERPRISE \
    --tier=db-f1-micro --storage-size=10GB --availability-type=zonal --quiet
fi
actual_region="$(gc sql instances describe "$INSTANCE" --format='value(region)')"
if [[ "$actual_region" != "$REGION" ]]; then
  echo "Existing Cloud SQL instance is in $actual_region, not $REGION." >&2
  exit 1
fi
if ! gc sql databases describe "$DATABASE" --instance="$INSTANCE" >/dev/null 2>&1; then
  gc sql databases create "$DATABASE" --instance="$INSTANCE" --quiet
fi

if [[ "$instance_exists" == true ]]; then
  if ! gc secrets describe "$ADMIN_SECRET" >/dev/null 2>&1 ||
     ! gc secrets describe "$WEB_SECRET" >/dev/null 2>&1; then
    echo "Existing instance is missing its DSN secrets; stop and inspect before changing passwords." >&2
    exit 1
  fi
else
  if gc secrets describe "$ADMIN_SECRET" >/dev/null 2>&1 ||
     gc secrets describe "$WEB_SECRET" >/dev/null 2>&1; then
    echo "DSN secrets already exist without this instance; refusing to reuse credentials." >&2
    exit 1
  fi
  admin_password="$(openssl rand -hex 24)"
  web_password="$(openssl rand -hex 24)"
  # gcloud SQL's password flag briefly exposes the generated value in process arguments.
  gc sql users set-password postgres --instance="$INSTANCE" \
    --password="$admin_password" --quiet
  gc secrets create "$ADMIN_SECRET" --replication-policy=automatic --quiet
  gc secrets create "$WEB_SECRET" --replication-policy=automatic --quiet
  printf '%s' "postgresql://postgres:${admin_password}@/${DATABASE}?host=/cloudsql/${CONNECTION}" |
    gc secrets versions add "$ADMIN_SECRET" --data-file=- --quiet >/dev/null
  printf '%s' "postgresql://research_reader:${web_password}@/${DATABASE}?host=/cloudsql/${CONNECTION}" |
    gc secrets versions add "$WEB_SECRET" --data-file=- --quiet >/dev/null
  unset admin_password web_password
fi

for binding in "$WEB_SECRET:$WEB_ACCOUNT" "$WEB_SECRET:$JOB_ACCOUNT" \
               "$ADMIN_SECRET:$JOB_ACCOUNT"; do
  secret="${binding%%:*}"
  identity="${binding#*:}"
  gc secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:$identity" --role=roles/secretmanager.secretAccessor \
    --quiet >/dev/null
done

echo "Building from Dockerfile and deploying Cloud Run privately first..."
gc run deploy "$SERVICE" --source="$ROOT" --region="$REGION" \
  --service-account="$WEB_ACCOUNT" --execution-environment=gen2 \
  --set-cloudsql-instances="$CONNECTION" \
  --set-env-vars="GCP_PROJECT=$PROJECT,GCP_LOCATION=$REGION,GENERATION_LOCATION=global,GEMINI_MODEL=gemini-3.5-flash" \
  --set-secrets="DATABASE_URL=$WEB_SECRET:latest" \
  --memory=1Gi --cpu=1 --min-instances=0 --max-instances=1 \
  --concurrency=4 --timeout=60s --no-allow-unauthenticated --quiet

image="$(gc run services describe "$SERVICE" --region="$REGION" \
  --format='value(spec.template.spec.containers[0].image)')"
if [[ -z "$image" ]]; then
  echo "The deployed service has no container image; cannot create indexing job." >&2
  exit 1
fi

gc run jobs deploy "$JOB" --image="$image" --region="$REGION" \
  --service-account="$JOB_ACCOUNT" \
  --set-cloudsql-instances="$CONNECTION" \
  --set-env-vars="GCP_PROJECT=$PROJECT,GCP_LOCATION=$REGION,GENERATION_LOCATION=global,GEMINI_MODEL=gemini-3.5-flash" \
  --set-secrets="DATABASE_URL=$ADMIN_SECRET:latest,WEB_DATABASE_URL=$WEB_SECRET:latest" \
  --command=python --args=backend/scripts/bootstrap.py \
  --memory=1Gi --cpu=1 --tasks=1 --max-retries=0 --task-timeout=900s --quiet
echo "Creating pgvector schema, reader role, and synthetic document index..."
gc run jobs execute "$JOB" --region="$REGION" --wait --quiet

if [[ "$ACCESS" == "public" ]]; then
  echo "Publishing the unauthenticated synthetic demo..."
  gc run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
    --member=allUsers --role=roles/run.invoker --quiet >/dev/null
fi

url="$(gc run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')"
if [[ -z "$url" ]]; then
  echo "Service URL not returned; inspect the Cloud Run service manually." >&2
  exit 1
fi
if [[ "$ACCESS" == "public" ]]; then
  curl --fail --silent --show-error --max-time 20 "$url/api/health" |
    python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ok"'
  curl --fail --silent --show-error --max-time 60 \
    -H 'Content-Type: application/json' \
    -d '{"question":"Show the DEMO demo quote","ticker":"DEMO"}' \
    "$url/api/chat" |
    python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] == "complete" and d["quote"]["is_synthetic"]'
  echo "Health and synthetic quote smoke tests passed."
fi
echo "Demo URL: $url"
if [[ "$ACCESS" != "public" ]]; then
  echo "Private URL: requires an authorized Cloud Run invoker identity."
fi
