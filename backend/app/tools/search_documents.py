import asyncio
import logging

from app.db import search_chunks
from app.gemini_client import embed

logger = logging.getLogger("copilot.tools.search_documents")


async def search_documents(
    query: str,
    ticker: str,
    document_type: str = "any",
    period: str | None = None,
    selection: str = "relevant",
    top_k: int = 5,
) -> dict:
    logger.info("embed query | ticker=%s type=%s selection=%s top_k=%s query=%r",
                ticker, document_type, selection, top_k, query[:100])
    vector = await asyncio.to_thread(embed, query, "RETRIEVAL_QUERY")
    logger.info("vector search | embedding_dims=%d", len(vector))
    rows = await asyncio.to_thread(
        search_chunks, ticker.upper(), vector, document_type, period, selection, top_k
    )
    logger.info("retrieved %d passages | docs=%s", len(rows),
                sorted({row["document_id"] for row in rows}))
    return {
        "passages": [
            {
                "chunk_id": row["chunk_id"],
                "document_id": row["document_id"],
                "ticker": row["ticker"],
                "title": row["title"],
                "document_type": row["document_type"],
                "document_date": row["document_date"].isoformat(),
                "period": row["period"],
                "section": row["section_or_page"],
                "excerpt": row["text"],
                "is_synthetic": row["is_synthetic"],
            }
            for row in rows
        ],
        "collection_scope": "synthetic demo documents only; not current SEC filings",
    }
