from contextlib import contextmanager

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.config import settings


@contextmanager
def connection():
    with psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=5) as conn:
        register_vector(conn)
        yield conn


def search_chunks(
    ticker: str,
    query_embedding: list[float],
    document_type: str = "any",
    period: str | None = None,
    selection: str = "relevant",
    top_k: int = 5,
) -> list[dict]:
    with connection() as conn:
        with conn.cursor() as cur:
            params: list = [ticker]
            filters = ["d.ticker = %s"]
            if document_type != "any":
                filters.append("d.document_type = %s")
                params.append(document_type)
            if period:
                filters.append("d.period = %s")
                params.append(period)
            where = " AND ".join(filters)
            if selection == "latest_available":
                cur.execute(
                    f"SELECT document_id FROM documents d WHERE {where} "
                    "ORDER BY d.document_date DESC, d.document_id DESC LIMIT 1",
                    params,
                )
                selected = cur.fetchone()
                if not selected:
                    return []
                where += " AND d.document_id = %s"
                params.append(selected["document_id"])

            cur.execute(
                "SELECT c.chunk_id, c.document_id, c.section_or_page, c.text, "
                "d.title, d.ticker, d.document_type, d.document_date, d.period, "
                "d.is_synthetic FROM chunks c JOIN documents d USING (document_id) "
                f"WHERE {where} ORDER BY c.embedding <=> %s LIMIT %s",
                [*params, Vector(query_embedding), top_k],
            )
            return [dict(row) for row in cur.fetchall()]


def database_ready() -> bool:
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM documents LIMIT 1")
        return True
    except psycopg.Error:
        return False
