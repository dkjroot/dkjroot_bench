"""Decode JSON received from outside the benchmark."""
import json
import math
import re

MAX_ARGUMENT_BYTES = 8192
MAX_REPLY_BYTES = 4 * 1024 * 1024
MAX_CALLS_PER_REPLY = 8
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def invalid_number(value):
    raise ValueError("Non-finite JSON number.")


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        invalid_number(value)
    return number


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=invalid_number, parse_float=finite_float)


def parse_arguments(raw):
    if not isinstance(raw, str):
        return None
    try:
        if len(raw.encode("utf-8")) > MAX_ARGUMENT_BYTES:
            return None
        return decode(raw)
    except (ValueError, RecursionError):
        return None


def valid_call(call):
    if not isinstance(call, dict) or call.keys() != {"id", "type", "function"}:
        return False
    call_id, function = call["id"], call["function"]
    return (call["type"] == "function"
            and isinstance(call_id, str) and 1 <= len(call_id) <= 256
            and call_id.isascii() and call_id.isprintable()
            and isinstance(function, dict) and function.keys() == {"name", "arguments"}
            and isinstance(function["name"], str) and TOOL_NAME.fullmatch(function["name"]) is not None
            and isinstance(function["arguments"], str))
