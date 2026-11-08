import asyncio
import logging

from pydantic import ValidationError

from app.registry import TOOL_REGISTRY

logger = logging.getLogger("copilot.executor")


async def execute(name: str, arguments: dict, remaining_seconds: float) -> dict:
    logger.info("dispatch tool=%s args=%s budget=%.1fs", name, arguments, remaining_seconds)
    entry = TOOL_REGISTRY.get(name)
    if entry is None or not entry.enabled:
        logger.warning("unknown tool requested: %s", name)
        return {"error": "unknown_tool", "message": f"Tool {name!r} is not available."}
    try:
        validated = entry.arguments_model.model_validate(arguments)
    except (ValidationError, ValueError, TypeError):
        logger.warning("invalid arguments for %s: %s", name, arguments)
        return {"error": "invalid_arguments", "message": "Check required fields and allowed values."}
    try:
        result = await asyncio.wait_for(
            entry.handler(**validated.model_dump()),
            timeout=min(entry.timeout_seconds, remaining_seconds),
        )
        if isinstance(result, dict) and result.get("error"):
            return result
        return entry.output_model.model_validate(result).model_dump()
    except asyncio.TimeoutError:
        return {"error": "tool_timeout", "message": f"{name} exceeded its time limit."}
    except Exception:
        return {"error": "tool_unavailable", "message": f"{name} is temporarily unavailable."}
