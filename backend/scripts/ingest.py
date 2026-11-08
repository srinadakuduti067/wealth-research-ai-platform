"""Manual ingestion: PYTHONPATH=backend python backend/scripts/ingest.py."""

import hashlib
import sys
from pathlib import Path

import yaml
from pgvector import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import connection, search_chunks  # noqa: E402
from app.gemini_client import embed  # noqa: E402


def parse_document(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML front matter")
    _, header, body = text.split("---", 2)
    metadata = yaml.safe_load(header)
    required = {
        "document_id", "ticker", "document_type", "title",
        "document_date", "period", "is_synthetic",
    }
    if not isinstance(metadata, dict) or not required.issubset(metadata):
        raise ValueError(f"{path}: missing required document metadata")
    if metadata["document_type"] not in ("filing", "earnings_transcript"):
        raise ValueError(f"{path}: invalid document_type")
    if metadata["is_synthetic"] is not True:
        raise ValueError(f"{path}: demo ingestion only accepts synthetic sources")
    return metadata, body.strip()


def split_sections(text: str, max_words: int = 550, overlap: int = 60):
    """Keep section headings with text; word counts approximate the token budget."""
    section = "Introduction"
    lines = []
    for line in [*text.splitlines(), "## END"]:
        if line.startswith("## "):
            if lines:
                words = (section + "\n" + "\n".join(lines)).split()
                step = max_words - overlap
                for offset in range(0, len(words), step):
                    excerpt = words[offset:offset + max_words]
                    if excerpt:
                        yield section, " ".join(excerpt)
                    if offset + max_words >= len(words):
                        break
            section, lines = line[3:].strip(), []
        else:
            lines.append(line)


def ingest_file(path: Path) -> int:
    doc, body = parse_document(path)
    chunks = []
    for index, (section, text) in enumerate(split_sections(body)):
        digest = hashlib.sha256(f"{doc['document_id']}:{index}:{text}".encode()).hexdigest()[:20]
        chunks.append((f"chunk-{digest}", section, text, embed(text, "RETRIEVAL_DOCUMENT")))
    if not chunks:
        raise ValueError(f"{path}: no searchable text")

    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (document_id, ticker, document_type, title, "
                "document_date, period, is_synthetic, source_file) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (document_id) DO UPDATE SET ticker=EXCLUDED.ticker, "
                "document_type=EXCLUDED.document_type, title=EXCLUDED.title, "
                "document_date=EXCLUDED.document_date, period=EXCLUDED.period, "
                "is_synthetic=EXCLUDED.is_synthetic, source_file=EXCLUDED.source_file",
                (
                    doc["document_id"], doc["ticker"].upper(), doc["document_type"],
                    doc["title"], doc["document_date"], doc["period"], True,
                    str(path.relative_to(ROOT)),
                ),
            )
            cur.execute("DELETE FROM chunks WHERE document_id = %s", (doc["document_id"],))
            cur.executemany(
                "INSERT INTO chunks (chunk_id, document_id, section_or_page, text, embedding) "
                "VALUES (%s,%s,%s,%s,%s)",
                [(chunk_id, doc["document_id"], section, text, Vector(vector))
                 for chunk_id, section, text, vector in chunks],
            )
    return len(chunks)


def main() -> None:
    files = sorted((ROOT / "data" / "documents").glob("*.md"))
    if not files:
        raise SystemExit("No documents found in data/documents/")
    for path in files:
        count = ingest_file(path)
        print(f"{path.name}: indexed {count} chunks")
    matches = search_chunks("DEMO", embed("customer concentration risks", "RETRIEVAL_QUERY"))
    print("Sample search:", [(r["title"], r["section_or_page"]) for r in matches[:3]])


if __name__ == "__main__":
    main()
