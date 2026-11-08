import asyncio
import logging
import time

from google.genai import types

logger = logging.getLogger("copilot.orchestrator")

from app.config import settings
from app.executor import execute
from app.gemini_client import client
from app.grounding import INSTRUCTIONS, cited_ids, safe_fallback
from app.registry import TOOL_REGISTRY

MAX_ROUNDS = 3
MAX_EXECUTIONS = 6
DEADLINE_SECONDS = 45


def declarations() -> list[types.Tool]:
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=entry.name,
                    description=entry.description,
                    parameters_json_schema=entry.input_schema,
                )
                for entry in TOOL_REGISTRY.values()
                if entry.enabled
            ]
        )
    ]


async def research(question: str, ticker: str | None = None) -> dict:
    prompt = question
    if ticker:
        prompt += f"\nSelected ticker in the UI (context only): {ticker.upper()}"
    history = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
    logger.info("request start | question=%r ticker=%s", question[:120], ticker)
    tools_used: list[str] = []
    sources: list[dict] = []
    source_by_chunk: dict[str, str] = {}
    quote = None
    errors: list[str] = []
    tool_error_codes: list[str] = []
    missing_evidence = False
    attempted = 0
    executions = 0
    quote_attempted = False
    final_text = ""
    start = time.monotonic()
    tool_config = types.GenerateContentConfig(
        system_instruction=INSTRUCTIONS, tools=declarations(), temperature=0.2
    )

    for round_number in range(MAX_ROUNDS + 1):
        remaining = DEADLINE_SECONDS - (time.monotonic() - start)
        if remaining <= 0:
            errors.append("The request timed out.")
            logger.warning("deadline exceeded after %.1fs", time.monotonic() - start)
            break
        logger.info(
            "model call round %d/%d | model=%s history_parts=%d budget=%.1fs",
            round_number + 1, MAX_ROUNDS + 1, settings.gemini_model,
            sum(len(c.parts or []) for c in history), remaining,
        )
        model_start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client().models.generate_content,
                    model=settings.gemini_model,
                    contents=history,
                    config=tool_config,
                ),
                timeout=remaining,
            )
        except Exception as exc:
            errors.append("The model is temporarily unavailable.")
            logger.error("model call failed | %s: %s", type(exc).__name__, str(exc)[:300])
            break
        logger.info("model responded in %.1fs", time.monotonic() - model_start)
        if not response.candidates or not response.candidates[0].content:
            errors.append("The model did not return an answer.")
            break
        candidate = response.candidates[0].content
        calls = [part.function_call for part in candidate.parts if part.function_call]
        if not calls:
            final_text = " ".join(part.text for part in candidate.parts if part.text).strip()
            logger.info("model returned final answer | %d chars, %d tools used",
                        len(final_text), len(tools_used))
            break
        logger.info("model requested %d tool call(s): %s", len(calls),
                    [(c.name, dict(c.args or {})) for c in calls])
        if round_number == MAX_ROUNDS or executions >= MAX_EXECUTIONS:
            errors.append("Tool limit reached; only available evidence can be shown.")
            logger.warning("tool limits hit | round=%d executions=%d", round_number, executions)
            break

        history.append(candidate)  # Retains Gemini's function-call IDs and thought signatures.
        result_parts = []
        for call in calls:
            if executions >= MAX_EXECUTIONS or time.monotonic() - start >= DEADLINE_SECONDS:
                errors.append("Tool limit reached; some requested evidence was not fetched.")
                break
            executions += 1
            attempted += 1
            name = call.name or ""
            if name in TOOL_REGISTRY and TOOL_REGISTRY[name].enabled and name not in tools_used:
                tools_used.append(name)
            if name == "get_stock_quote":
                quote_attempted = True
            tool_start = time.monotonic()
            result = await execute(
                name, call.args if isinstance(call.args, dict) else {},
                DEADLINE_SECONDS - (time.monotonic() - start),
            )
            logger.info("tool %s returned in %.1fs | keys=%s%s", name,
                        time.monotonic() - tool_start,
                        sorted(result.keys()),
                        f" passages={len(result['passages'])}" if "passages" in result else "")
            if result.get("error"):
                errors.append(result["message"])
                tool_error_codes.append(result["error"])
            if name == "search_documents":
                if not result.get("error") and not result.get("passages"):
                    missing_evidence = True
                for passage in result.get("passages", []):
                    chunk_id = passage["chunk_id"]
                    if chunk_id not in source_by_chunk:
                        source_id = f"S{len(sources) + 1}"
                        source_by_chunk[chunk_id] = source_id
                        sources.append({**passage, "source_id": source_id})
                    passage["source_id"] = source_by_chunk[chunk_id]
            if name == "get_stock_quote" and result.get("quote"):
                quote = result["quote"]
            result_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name, response={"result": result}, id=call.id
                    )
                )
            )
        if result_parts:
            history.append(types.Content(role="tool", parts=result_parts))
        else:
            break

    if quote and not sources and not errors and not missing_evidence:
        final_text = safe_fallback([], quote)
    valid_ids = {source["source_id"] for source in sources}
    invalid_ids = cited_ids(final_text) - valid_ids
    missing_citations = bool(sources and not cited_ids(final_text))
    unsupported_portion = (missing_evidence or quote_attempted and not quote) and bool(
        sources or quote
    )
    if invalid_ids or missing_citations or (not final_text and (sources or quote)) or unsupported_portion:
        final_text = safe_fallback(sources, quote)
        if invalid_ids or missing_citations:
            errors.append("Generated citations could not be verified against the returned passages.")
    if not sources and not quote:
        if attempted == 0 and final_text and not errors:
            return {
                "status": "clarification_required",
                "answer": "Please specify a demo ticker (AAPL or DEMO) and a research question.",
                "tools_used": tools_used,
                "sources": [],
                "quote": None,
            }
        return {
            "status": (
                "error"
                if (errors and not attempted)
                or any(code != "unknown_ticker" for code in tool_error_codes)
                else "insufficient_evidence"
            ),
            "answer": (
                "Research is temporarily unavailable. Please retry."
                if (errors and not attempted)
                or any(code != "unknown_ticker" for code in tool_error_codes)
                else "No supporting evidence was found in the synthetic demo collection. "
                "Please check the ticker or try a different question."
            ),
            "tools_used": tools_used,
            "sources": [],
            "quote": None,
        }
    if errors and not final_text:
        final_text = safe_fallback(sources, quote)
    if missing_evidence:
        final_text += " Some requested document evidence was not found in the demo collection."
    logger.info("request done | status=%s tools=%s sources=%d quote=%s errors=%s elapsed=%.1fs",
                "partial" if errors or missing_evidence else "complete", tools_used,
                len(sources), bool(quote), errors, time.monotonic() - start)
    return {
        "status": "partial" if errors or missing_evidence else "complete",
        "answer": final_text,
        "tools_used": tools_used,
        "sources": sources,
        "quote": quote,
    }
