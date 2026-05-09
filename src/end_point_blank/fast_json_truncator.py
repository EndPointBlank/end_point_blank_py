from __future__ import annotations

import json
from typing import Any

MAX_BYTES = 10_000
MAX_DEPTH = 5
MAX_LIST = 20
MAX_STRING = 200
MAX_KEYS = 20


class FastJsonTruncator:
    """
    Recursively prunes a JSON-serialisable value so it fits within a byte budget,
    then serialises it to a JSON string.

    Equivalent to the Ruby gem's ``FastJsonTruncator``.
    """

    @classmethod
    def truncate(cls, data: Any, limit: int = MAX_BYTES) -> str:
        pruned = cls._prune(data, 0)
        result = json.dumps(pruned, ensure_ascii=False)
        return cls._ensure_limit(result, limit)

    @classmethod
    def _prune(cls, value: Any, depth: int) -> Any:
        if depth > MAX_DEPTH:
            return "[truncated]"

        if isinstance(value, dict):
            result: dict = {}
            for i, (k, v) in enumerate(value.items()):
                if i >= MAX_KEYS:
                    break
                result[k] = cls._prune(v, depth + 1)
            return result

        if isinstance(value, list):
            return [cls._prune(v, depth + 1) for v in value[:MAX_LIST]]

        if isinstance(value, str):
            encoded = value.encode("utf-8")
            if len(encoded) > MAX_STRING:
                return encoded[:MAX_STRING].decode("utf-8", errors="ignore") + "..."
            return value

        return value

    @staticmethod
    def _ensure_limit(json_str: str, limit: int) -> str:
        if len(json_str.encode("utf-8")) <= limit:
            return json_str
        sliced = json_str.encode("utf-8")[: limit - 20].decode("utf-8", errors="ignore")
        return sliced + '...,"truncated":true}'
