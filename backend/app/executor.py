import asyncio

from pydantic import ValidationError

from app.registry import TOOL_REGISTRY


async def execute(name: str, arguments: dict, remaining_seconds: float) -> dict:
    entry = TOOL_REGISTRY.get(name)
    if entry is None or not entry.enabled:
        return {"error": "unknown_tool", "message": f"Tool {name!r} is not available."}
    try:
        validated = entry.arguments_model.model_validate(arguments)
    except (ValidationError, ValueError, TypeError):
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
