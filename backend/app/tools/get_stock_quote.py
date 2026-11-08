import asyncio
import csv
import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app.config import settings

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1m"
STOOQ_URL = "https://stooq.com/q/l/?s={ticker}.us&f=sd2t2ohlcv&h&e=csv"
USER_AGENT = "Mozilla/5.0 (compatible; wealth-research-demo/0.2)"
HTTP_TIMEOUT = 6


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return response.read().decode("utf-8")


def _yahoo_quote(symbol: str) -> dict | None:
    payload = json.loads(_fetch(YAHOO_CHART_URL.format(ticker=symbol)))
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        return None
    meta = results[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not isinstance(price, (int, float)):
        return None
    market_time = meta.get("regularMarketTime")
    offset = meta.get("gmtoffset", 0)
    timestamp = (
        datetime.fromtimestamp(market_time + offset, tz=timezone.utc).isoformat()
        if isinstance(market_time, int)
        else datetime.now(timezone.utc).isoformat()
    )
    market_state = "unknown"
    trading = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    if isinstance(market_time, int) and trading.get("start") and trading.get("end"):
        market_state = "regular" if trading["start"] <= market_time < trading["end"] else "closed"
    quote = {
        "ticker": symbol,
        "price": round(price, 4),
        "currency": meta.get("currency", "USD"),
        "exchange": meta.get("exchangeName", ""),
        "provider": "yahoo-finance-chart",
        "quote_timestamp": timestamp,
        "quote_type": "live" if market_state == "regular" else "last_close",
        "market_state": market_state,
        "delay_seconds": None,
        "is_synthetic": False,
    }
    if isinstance(previous, (int, float)) and previous:
        quote["previous_close"] = round(previous, 4)
        quote["change"] = round(price - previous, 4)
        quote["change_percent"] = round((price - previous) / previous * 100, 2)
    return quote


def _stooq_quote(symbol: str) -> dict | None:
    rows = list(csv.reader(io.StringIO(_fetch(STOOQ_URL.format(ticker=symbol.lower())))))
    if len(rows) < 2 or "close" not in [field.strip().lower() for field in rows[0]]:
        return None
    record = dict(zip([f.strip().lower() for f in rows[0]], rows[1]))
    try:
        price = float(record["close"])
        opened = float(record["open"])
    except (ValueError, KeyError):
        return None
    timestamp = f"{record.get('date', '')}T{record.get('time', '')}Z"
    return {
        "ticker": symbol,
        "price": round(price, 4),
        "currency": "USD",
        "exchange": "US",
        "provider": "stooq",
        "quote_timestamp": timestamp,
        "quote_type": "last_trade_or_close",
        "market_state": "unknown",
        "delay_seconds": 900,
        "is_synthetic": False,
        "change": round(price - opened, 4),
        "change_percent": round((price - opened) / opened * 100, 2) if opened else None,
    }


def _live_quote(symbol: str) -> dict | None:
    for provider in (_yahoo_quote, _stooq_quote):
        try:
            quote = provider(symbol)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
        if quote:
            return quote
    return None


def _fixture_quote(symbol: str) -> dict | None:
    quote = json.loads(settings.fixture_file.read_text(encoding="utf-8")).get(symbol)
    if quote and quote.get("is_synthetic") is not True:
        raise ValueError("Fixture quote must be explicitly labeled synthetic.")
    return quote


async def get_stock_quote(ticker: str) -> dict:
    symbol = ticker.upper()
    fixture = _fixture_quote(symbol)
    if fixture and fixture.get("synthetic_only"):
        return {"quote": fixture, "warning": "Synthetic demo fixture; not live market data."}
    if settings.quotes_mode != "fixture":
        live = await asyncio.to_thread(_live_quote, symbol)
        if live:
            live["fetched_at"] = datetime.now(timezone.utc).isoformat()
            return {"quote": live, "warning": "Third-party public feed; verify against a licensed source."}
    if fixture:
        return {"quote": fixture, "warning": "Synthetic demo fixture; not live market data."}
    return {"error": "unknown_ticker", "message": f"No live or demo quote found for {symbol}."}
