"""Generic MavSDK value-object -> JSON-able dict conversion for tool results."""

import enum
import math
from typing import Any

_MAX_DEPTH = 6


def to_jsonable(value: Any, _depth: int = 0) -> Any:
    """Recursively convert MavSDK value objects (plain attribute classes),
    enums, and containers into JSON-serializable structures."""
    if _depth > _MAX_DEPTH:
        return str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in value.items()}
    if hasattr(value, "__dict__") and value.__dict__:
        return {k: to_jsonable(v, _depth + 1) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)
