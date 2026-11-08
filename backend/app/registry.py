from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, Field, field_validator

from app.tools.get_stock_quote import get_stock_quote
from app.tools.search_documents import search_documents


class SearchArguments(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    ticker: str = Field(min_length=1, max_length=12)
    document_type: str = Field(default="any", pattern="^(filing|earnings_transcript|any)$")
    period: str | None = Field(default=None, max_length=30)
    selection: str = Field(default="relevant", pattern="^(relevant|latest_available)$")
    top_k: int = Field(default=5, ge=1)

    @field_validator("top_k")
    @classmethod
    def cap_top_k(cls, value: int) -> int:
        return min(value, 8)


class QuoteArguments(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)


class SearchOutput(BaseModel):
    passages: list[dict]
    collection_scope: str


class QuoteOutput(BaseModel):
    quote: dict
    warning: str


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    arguments_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable
    enabled: bool = True
    timeout_seconds: float = 8


TOOL_REGISTRY = {
    "search_documents": ToolEntry(
        name="search_documents",
        description=(
            "Search synthetic filings and earnings transcripts for company facts, risks, "
            "earnings, or summaries. Provide the ticker and a focused search query. "
            "Use selection=latest_available to select the newest matching document "
            "in this collection before searching its passages."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Focused question or search terms."},
                "ticker": {"type": "string", "description": "Ticker from the question or UI."},
                "document_type": {
                    "type": "string",
                    "enum": ["filing", "earnings_transcript", "any"],
                },
                "period": {"type": "string", "description": "Exact reporting period, e.g. FY2026."},
                "selection": {"type": "string", "enum": ["relevant", "latest_available"]},
                "top_k": {"type": "integer", "description": "Number of passages, 1 to 8."},
            },
            "required": ["query", "ticker"],
        },
        output_schema={"passages": "list of source passages or empty list"},
        arguments_model=SearchArguments,
        output_model=SearchOutput,
        handler=search_documents,
    ),
    "get_stock_quote": ToolEntry(
        name="get_stock_quote",
        description=(
            "Get a quote for a ticker. For real listed tickers it returns live market "
            "data from a public feed (price, change, percent change, provider, timestamp). "
            "For demo tickers it returns a clearly labeled synthetic fixture. "
            "Use for price and percent-move questions; report provider and timestamp."
        ),
        input_schema={
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
        output_schema={"quote": "synthetic price, currency, provider and fixture time"},
        arguments_model=QuoteArguments,
        output_model=QuoteOutput,
        handler=get_stock_quote,
        timeout_seconds=5,
    ),
}
