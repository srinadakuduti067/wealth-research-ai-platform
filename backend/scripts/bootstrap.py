"""One-off Cloud Run Job: create schema, grant reader access, ingest demo files."""

from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from app.config import ROOT, settings
from scripts.ingest import main as ingest

READER_ROLE = "research_reader"


def bootstrap() -> None:
    web_password = urlsplit(settings.web_database_url).password
    if not web_password:
        raise ValueError("WEB_DATABASE_URL must contain the reader's password.")

    # Connect without pgvector registration: the extension may not exist yet.
    with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            schema = (Path(ROOT) / "db" / "schema.sql").read_text(encoding="utf-8")
            for statement in schema.split(";"):
                if statement.strip():
                    cur.execute(statement)
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER_ROLE,))
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(READER_ROLE), sql.Literal(web_password)
                    )
                )
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(READER_ROLE), sql.Literal(web_password)
                    )
                )
            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(conn.info.dbname), sql.Identifier(READER_ROLE)
                )
            )
            cur.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(READER_ROLE))
            )
            cur.execute(
                sql.SQL("GRANT SELECT ON documents, chunks TO {}").format(
                    sql.Identifier(READER_ROLE)
                )
            )

    ingest()


if __name__ == "__main__":
    bootstrap()
