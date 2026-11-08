from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from app import db, main, orchestrator
from app.config import ROOT
from app.executor import execute
from app.grounding import cited_ids
from app.tools import get_stock_quote as quote_module
from app.tools.get_stock_quote import get_stock_quote
from backend.scripts.ingest import parse_document, split_sections


def model_response(*parts):
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=list(parts)))]
    )


class FakeModels:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.histories = []

    def generate_content(self, **kwargs):
        self.histories.append(kwargs["contents"][:])
        return next(self.responses)


def fake_client(monkeypatch, *responses):
    models = FakeModels(responses)
    monkeypatch.setattr(orchestrator, "client", lambda: SimpleNamespace(models=models))
    return models


@pytest.mark.asyncio
async def test_quote_fixture_fallback_and_unknown_ticker(monkeypatch):
    monkeypatch.setattr(quote_module, "_live_quote", lambda symbol: None)
    quote = (await get_stock_quote("aapl"))["quote"]
    assert quote["is_synthetic"] and quote["provider"] == "synthetic-demo-fixture"
    assert (await get_stock_quote("NOPE"))["error"] == "unknown_ticker"


YAHOO_PAYLOAD = {
    "chart": {"result": [{"meta": {
        "regularMarketPrice": 250.5,
        "chartPreviousClose": 245.0,
        "currency": "USD",
        "exchangeName": "NMS",
        "regularMarketTime": 1794157200,
        "gmtoffset": -18000,
        "currentTradingPeriod": {"regular": {"start": 1794150000, "end": 1794168000}},
    }}]}
}


@pytest.mark.asyncio
async def test_live_quote_maps_provider_fields_and_percent_move(monkeypatch):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "yahoo" in url:
            return __import__("json").dumps(YAHOO_PAYLOAD)
        raise OSError("skip stooq")

    monkeypatch.setattr(quote_module, "_fetch", fake_fetch)
    result = await get_stock_quote("aapl")
    quote = result["quote"]
    assert quote["is_synthetic"] is False
    assert quote["provider"] == "yahoo-finance-chart"
    assert quote["quote_type"] == "live"
    assert quote["price"] == 250.5
    assert quote["change_percent"] == 2.24
    assert calls == [quote_module.YAHOO_CHART_URL.format(ticker="AAPL")]


@pytest.mark.asyncio
async def test_live_failure_falls_back_to_stooq_then_fixture(monkeypatch):
    def fake_fetch(url):
        if "yahoo" in url:
            raise OSError("yahoo down")
        if "stooq" in url:
            return "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,2026-11-07,21:59:00,100.0,102.0,99.5,101.0,123456\n"
        raise AssertionError(url)

    monkeypatch.setattr(quote_module, "_fetch", fake_fetch)
    quote = (await get_stock_quote("AAPL"))["quote"]
    assert quote["provider"] == "stooq" and quote["is_synthetic"] is False
    assert quote["price"] == 101.0 and quote["change_percent"] == 1.0


@pytest.mark.asyncio
async def test_executor_rejects_unknown_and_malformed_tools():
    assert (await execute("not_registered", {}, 5))["error"] == "unknown_tool"
    assert (await execute("search_documents", {"ticker": "DEMO"}, 5))["error"] == "invalid_arguments"


def test_manual_ingestion_source_metadata_and_sections():
    documents = list((ROOT / "data" / "documents").glob("*.md"))
    assert len(documents) >= 5
    for document in documents:
        metadata, body = parse_document(document)
        assert metadata["is_synthetic"] is True
        assert list(split_sections(body))


def test_latest_document_is_selected_before_vector_search(monkeypatch):
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, parameters):
            queries.append((query, parameters))

        def fetchone(self):
            return {"document_id": "newest-doc"}

        def fetchall(self):
            return []

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def fake_connection():
        yield Connection()

    monkeypatch.setattr(db, "connection", fake_connection)
    db.search_chunks("AAPL", [0.1] * 768, document_type="filing", selection="latest_available")
    assert "ORDER BY d.document_date DESC" in queries[0][0]
    assert "d.document_id = %s" in queries[1][0]
    assert "newest-doc" in queries[1][1]


@pytest.mark.asyncio
async def test_quote_tool_roundtrip_preserves_call_id(monkeypatch):
    monkeypatch.setattr(quote_module, "_live_quote", lambda symbol: None)
    models = fake_client(
        monkeypatch,
        model_response(
            types.Part(function_call=types.FunctionCall(
                name="get_stock_quote", args={"ticker": "AAPL"}, id="quote-call-1"
            ))
        ),
        model_response(types.Part.from_text(text="The synthetic fixture is not live.")),
    )
    result = await orchestrator.research("What is AAPL's demo quote?")
    assert result["status"] == "complete"
    assert result["quote"]["is_synthetic"] is True
    assert result["tools_used"] == ["get_stock_quote"]
    assert models.histories[1][-1].parts[0].function_response.id == "quote-call-1"


@pytest.mark.asyncio
async def test_document_and_quote_calls_return_distinct_evidence(monkeypatch):
    monkeypatch.setattr(quote_module, "_live_quote", lambda symbol: None)
    fake_client(
        monkeypatch,
        model_response(
            types.Part(function_call=types.FunctionCall(
                name="search_documents",
                args={"query": "risks", "ticker": "DEMO", "selection": "latest_available"},
            )),
            types.Part(function_call=types.FunctionCall(
                name="get_stock_quote", args={"ticker": "DEMO"}
            )),
        ),
        model_response(types.Part.from_text(text="Synthetic customer concentration [S1]. Demo quote shown separately.")),
    )

    async def execute_fake(name, arguments, remaining_seconds):
        if name == "get_stock_quote":
            return await get_stock_quote("DEMO")
        return {"passages": [{
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "title": "Synthetic DEMO filing",
            "document_date": "2026-09-30",
            "period": "FY2026",
            "section": "Risk Factors",
            "excerpt": "Customer concentration risk.",
            "is_synthetic": True,
        }]}

    monkeypatch.setattr(orchestrator, "execute", execute_fake)
    response = await orchestrator.research("DEMO quote and filing risks?")
    assert response["status"] == "complete"
    assert response["sources"][0]["source_id"] == "S1"
    assert response["quote"]["is_synthetic"] is True
    assert set(response["tools_used"]) == {"search_documents", "get_stock_quote"}


@pytest.mark.asyncio
async def test_invalid_citations_are_replaced_by_safe_fallback(monkeypatch):
    fake_client(
        monkeypatch,
        model_response(types.Part(function_call=types.FunctionCall(
            name="search_documents", args={"query": "risks", "ticker": "DEMO"}
        ))),
        model_response(types.Part.from_text(text="Unverified claim [S999].")),
    )

    async def execute_fake(*args):
        return {"passages": [{"chunk_id": "chunk-1", "source_id": "",
                              "title": "Synthetic", "excerpt": "A risk.", "is_synthetic": True}]}

    monkeypatch.setattr(orchestrator, "execute", execute_fake)
    response = await orchestrator.research("Risks for DEMO?")
    assert response["status"] == "partial"
    assert cited_ids(response["answer"]) == {"S1"}
    assert "Unverified claim" not in response["answer"]


@pytest.mark.asyncio
async def test_empty_search_returns_insufficient_evidence(monkeypatch):
    fake_client(
        monkeypatch,
        model_response(types.Part(function_call=types.FunctionCall(
            name="search_documents", args={"query": "risks", "ticker": "NOPE"}
        ))),
        model_response(types.Part.from_text(text="No evidence available.")),
    )

    async def empty(*args):
        return {"passages": []}

    monkeypatch.setattr(orchestrator, "execute", empty)
    response = await orchestrator.research("What risks does NOPE have?")
    assert response["status"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_unknown_quote_fixture_returns_insufficient_evidence(monkeypatch):
    fake_client(
        monkeypatch,
        model_response(types.Part(function_call=types.FunctionCall(
            name="get_stock_quote", args={"ticker": "NOPE"}
        ))),
        model_response(types.Part.from_text(text="No fixture quote available.")),
    )
    response = await orchestrator.research("Quote for NOPE?")
    assert response["status"] == "insufficient_evidence"
    assert response["quote"] is None


@pytest.mark.asyncio
async def test_missing_document_evidence_never_turns_into_uncited_claim(monkeypatch):
    fake_client(
        monkeypatch,
        model_response(
            types.Part(function_call=types.FunctionCall(
                name="get_stock_quote", args={"ticker": "AAPL"}
            )),
            types.Part(function_call=types.FunctionCall(
                name="search_documents", args={"query": "risk", "ticker": "AAPL"}
            )),
        ),
        model_response(types.Part.from_text(text="The filing reports an invented risk.")),
    )

    async def execute_fake(name, arguments, remaining_seconds):
        return await get_stock_quote("AAPL") if name == "get_stock_quote" else {"passages": []}

    monkeypatch.setattr(orchestrator, "execute", execute_fake)
    response = await orchestrator.research("AAPL quote and filing risks?")
    assert response["status"] == "partial"
    assert "invented risk" not in response["answer"]
    assert "not found" in response["answer"]


@pytest.mark.asyncio
async def test_model_cannot_execute_more_than_six_tools(monkeypatch):
    models = FakeModels(iter([
        model_response(*[
            types.Part(function_call=types.FunctionCall(
                name="get_stock_quote", args={"ticker": "AAPL"}
            )) for _ in range(3)
        ]) for _ in range(4)
    ]))
    monkeypatch.setattr(orchestrator, "client", lambda: SimpleNamespace(models=models))
    count = 0

    async def counted(*args):
        nonlocal count
        count += 1
        return await get_stock_quote("AAPL")

    monkeypatch.setattr(orchestrator, "execute", counted)
    response = await orchestrator.research("Quote AAPL?")
    assert count == 6
    assert response["status"] == "partial"


def test_chat_endpoint_contract(monkeypatch):
    async def mocked_research(question, ticker):
        return {"status": "complete", "answer": "Demo response", "tools_used": [],
                "sources": [], "quote": None}

    monkeypatch.setattr(main, "research", mocked_research)
    client = TestClient(main.app)
    response = client.post("/api/chat", json={"question": "Explain DEMO", "ticker": "DEMO"})
    assert response.status_code == 200
    assert response.json()["answer"] == "Demo response"
    assert client.post("/api/chat", json={"question": ""}).status_code == 422
    assert client.get("/").status_code == 200
