# Wealth Research Copilot — synthetic MVP

Local-first research demo implementing the v0.2 MVP specification. A single FastAPI app serves a static chat page. Gemini on Vertex AI selects registered tools; the backend executes document search and labeled fixture quotes, returns tool results to Gemini, then checks citations before answering. There is **no authentication**: run on localhost only. All sample filings/transcripts and quotes are fictional; the demo cannot supply investment advice or live market data.

## Architecture

```text
browser (frontend/index.html) -> POST /api/chat (FastAPI)
  -> orchestrator -> Gemini 3.5 Flash, global endpoint (function declarations)
      -> tool calls -> registry/executor
          -> search_documents -> regional query embedding -> Postgres/pgvector chunks
          -> get_stock_quote  -> live public quote feed (Yahoo chart -> Stooq)
                               -> data/quotes_fixture.json for synthetic demo tickers
      -> tool results -> Gemini -> cited answer + quote + sources -> browser
```

The tool loop is bounded to three execution rounds, six executions, and 45 seconds. `selection=latest_available` selects a document by its date *before* searching chunks. The document index is seeded manually; there is no crawler, upload flow, persistence, or security boundary.

**Quotes:** `get_stock_quote` returns real market data for listed tickers from a public feed (Yahoo chart API, Stooq fallback) — provider, timestamp, and change/percent are returned verbatim and labeled `is_synthetic: false`. Demo-only tickers (`DEMO`) and feed failures fall back to the labeled synthetic fixture (`synthetic_only` entries always stay synthetic; `QUOTES_MODE=fixture` disables live calls for offline dev). The feed is unofficial and may be delayed — it demonstrates the live tool-calling flow but is **not** a licensed real-time source.

## Local setup

Requirements: Python 3.11+ (3.13 works), Docker Desktop running, a GCP project with Vertex AI API enabled and permission to invoke the configured Gemini and embedding models. The following GCP auth and Vertex calls use your own project quota/billing:

```sh
cp .env.example .env
# Set GCP_PROJECT in .env and confirm GCP_LOCATION/model availability.
gcloud auth application-default login
python3.13 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
docker compose up -d db
PYTHONPATH=backend .venv/bin/python backend/scripts/ingest.py
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The DB schema is initialized on first container creation; if using an existing database, apply `db/schema.sql` yourself. `GET /api/health` checks DB access; `POST /api/chat` accepts `{\"question\":\"What risks are listed for DEMO?\",\"ticker\":\"DEMO\"}` and responds with `status`, `answer`, `tools_used`, `sources`, and `quote`. Check `GET /docs` for the API shape.

Example questions:
- What risks are listed for DEMO?
- What is AAPL's quote?
- Give me AAPL's quote and latest available filing highlights.
- What did management say about margins? (select `DEMO` in the ticker field.)

`data/documents/*.md` are YAML-front-matter synthetic fixtures. Run `ingest.py` again after editing them. Each document is atomically replaced in the database to avoid duplicate chunks; deleting a local file does *not* delete its existing database record. To use a different embedding dimension, change the vector column dimension in `db/schema.sql` and rebuild the database index using a new model. **Never treat fixture quotes or filings as real.**

## Tests

```sh
PYTHONPATH=backend .venv/bin/python -m pytest tests -q
```

Unit tests mock the model and DB; a true integration run additionally requires Docker, ADC, Vertex access, and ingestion. Do not expose the unauthenticated prototype to real users or with real data.

## GCP deployment (optional, billable)

**Current synthetic demo (stylesense-ai-dev, us-central1):**
https://wealth-research-demo-fytibbfixq-uc.a.run.app
The UI, health check, quote, document, and combined question routes were verified
on November 8, 2026. Generation uses the Vertex AI global endpoint; embeddings,
Cloud Run, and Cloud SQL use `us-central1`. The URL is public and unauthenticated.

`scripts/deploy_gcp.sh` prepares a Cloud Run service (same static UI + FastAPI app),
a single small Cloud SQL PostgreSQL 16 instance with pgvector, two service accounts,
two Secret Manager database DSNs, and a one-off Cloud Run Job to create the schema
and index the five synthetic documents. The web app connects as a **read-only**
database role. The indexing job uses a separate admin credential to create the
schema and reader role. Cloud Run source deployment builds the included Dockerfile;
Docker Desktop is not required for this path.

The default is offline **dry-run**; it does not contact GCP:

```sh
bash scripts/deploy_gcp.sh --project stylesense-ai-dev --region us-central1 --public
```

To actually create resources and temporarily publish a URL, first review the script,
confirm billing and organization policy, ensure `gcloud auth login` is active with
permissions for service enablement, Cloud SQL, Cloud Run, Cloud Build, Secret Manager,
IAM binding and service-account impersonation, and verify that Vertex AI supports
`gemini-3.5-flash` on the global endpoint and `text-embedding-005` in the
selected region:

```sh
CONFIRM_PROJECT=stylesense-ai-dev ALLOW_PUBLIC_DEMO=I_ACCEPT_PUBLIC_COSTS \
  bash scripts/deploy_gcp.sh --project stylesense-ai-dev --region us-central1 \
  --apply --public
```

The script uses the project passed explicitly (not the active `gcloud` default),
deploys the service privately first, runs the one-off ingestion job, then grants
`allUsers` Cloud Run Invoker and checks `/api/health` plus a quote question.
It prints the Cloud Run URL **only after the deployment completes**. It may take
several minutes to provision Cloud SQL on a fresh project.

**Security and cost:** this public option has no app login or rate limit; anyone
with the URL can generate billable Vertex requests. It is only suitable for
synthetic demo data. Cloud SQL continues to incur charges even when Cloud Run
scales to zero. The script does not auto-delete resources. Restrict access or
delete the resources explicitly when finished; never add real/client data.
The first `gcloud sql users set-password` invocation passes a generated password
as a short-lived CLI argument; use a dedicated project/workstation for the demo.
If provisioning stops partway through, inspect the partially created resources
and their costs before rerunning; existing databases are never reset automatically.

## Publish source to GitHub

`scripts/publish_github.py` creates the **public** repository
`srinadakuduti067/wealth-research-copilot` and commits a snapshot of project
source, tests, synthetic data, and this script. It does not change the local
Git working tree or interact with the GCP deployment. Local `.env`, virtual
environments, and generated caches are excluded. It refuses an existing
repository rather than overwriting it. GitHub initializes the repository with
a README; the project snapshot is the following commit.

Revoke any token accidentally shared in a message before using a **new** token
with repository creation permissions. Keep the new token out of files and chat:

```sh
python3 scripts/publish_github.py
read -s GITHUB_TOKEN
export GITHUB_TOKEN
python3 scripts/publish_github.py --apply
unset GITHUB_TOKEN
```

The first command is an offline dry-run that lists the files to publish.
The `read` command prompts for the token without displaying it. The `--apply`
command creates the public repository and verifies its uploaded files and
commit. Run this from the project directory in a shell; on shells without
`read -s`, use an equivalent silent input method.
