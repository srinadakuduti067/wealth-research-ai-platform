import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import ROOT
from scripts import bootstrap as bootstrap_module


SCRIPT = Path(ROOT) / "scripts" / "deploy_gcp.sh"


def test_deploy_is_offline_by_default():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--project", "stylesense-ai-dev", "--public"],
        capture_output=True, text=True, check=True,
    )
    assert "No GCP calls made" in result.stdout
    assert "db-f1-micro" in result.stdout


def test_apply_refuses_without_explicit_project_confirmation():
    env = {key: value for key, value in os.environ.items()
           if key not in ("CONFIRM_PROJECT", "ALLOW_PUBLIC_DEMO")}
    result = subprocess.run(
        ["bash", str(SCRIPT), "--project", "stylesense-ai-dev", "--apply", "--public"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2
    assert "CONFIRM_PROJECT" in result.stderr


def test_bootstrap_grants_reader_and_runs_ingestion(monkeypatch):
    statements = []
    ingested = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement, params=None):
            statements.append(str(statement))

        def fetchone(self):
            return None

    class Connection:
        info = SimpleNamespace(dbname="research")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(
        bootstrap_module, "settings",
        SimpleNamespace(
            database_url="postgresql://postgres:admin@/research?host=/cloudsql/demo",
            web_database_url="postgresql://research_reader:secret@/research?host=/cloudsql/demo",
        ),
    )
    monkeypatch.setattr(bootstrap_module.psycopg, "connect", lambda *a, **kw: Connection())
    monkeypatch.setattr(bootstrap_module, "ingest", lambda: ingested.append(True))
    bootstrap_module.bootstrap()
    assert any("CREATE EXTENSION IF NOT EXISTS vector" in stmt for stmt in statements)
    assert any("GRANT SELECT ON documents, chunks" in stmt for stmt in statements)
    assert ingested == [True]


def test_bootstrap_requires_reader_secret(monkeypatch):
    monkeypatch.setattr(bootstrap_module, "settings", SimpleNamespace(web_database_url=""))
    with pytest.raises(ValueError, match="WEB_DATABASE_URL"):
        bootstrap_module.bootstrap()
