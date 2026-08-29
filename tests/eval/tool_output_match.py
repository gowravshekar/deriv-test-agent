"""Deterministic match of the final response against tool output in the trace.

Live values (the clock) have no frozen golden answer, so a reference-based LLM
judge cannot grade them. This metric compares the response to what the tools
actually returned in the same trace instead.
"""

import re

_TIME_OK = re.compile(r"\d{4}-\d{2}-\d{2} (\d{2}):(\d{2}):\d{2} ([A-Z]{2,5})")
_WEATHER = re.compile(r"It's (\d+) degrees and (\w+)")
_NO_TZ = "don't have timezone information"
_FABRICATED = re.compile(r"\b\d{1,2}:\d{2}\b|\bUTC\s?[+-]\s?\d{1,2}\b|\bJST\b")


def _final_text(instance):
    response = instance.get("response") or {}
    if isinstance(response, str):
        return response
    parts = response.get("parts") or []
    return " ".join(part.get("text") or "" for part in parts)


def _tool_results(instance):
    results = []
    for turn in (instance.get("agent_data") or {}).get("turns", []):
        for event in turn.get("events", []):
            for part in (event.get("content") or {}).get("parts", []):
                call = part.get("function_response")
                if not call:
                    continue
                payload = call.get("response") or {}
                results.append((call.get("name", ""), str(payload.get("result", ""))))
    return results


def evaluate(instance):
    text = _final_text(instance)
    checks = []

    for _name, result in _tool_results(instance):
        weather = _WEATHER.search(result)
        clock = _TIME_OK.search(result)
        if weather is not None:
            degrees, condition = weather.group(1), weather.group(2)
            checks.append((degrees in text, f"reports {degrees} degrees"))
            checks.append((condition.lower() in text.lower(), f"reports '{condition}'"))
        elif clock is not None:
            hour, minute, tz = int(clock.group(1)), clock.group(2), clock.group(3)
            wanted = {
                f"{hour}:{minute}",
                f"{hour:02d}:{minute}",
                f"{hour % 12 or 12}:{minute}",
            }
            checks.append(
                (
                    any(candidate in text for candidate in wanted),
                    f"reports tool time {hour:02d}:{minute}",
                )
            )
            checks.append((tz in text, f"reports timezone {tz}"))
        elif _NO_TZ in result:
            checks.append(
                (
                    _FABRICATED.search(text) is None,
                    "states no clock time or offset the tool never returned",
                )
            )

    if not checks:
        return {"score": 1.0, "explanation": "No tool output to match against."}

    failed = [description for passed, description in checks if not passed]
    score = (len(checks) - len(failed)) / len(checks)
    if not failed:
        return {"score": score, "explanation": "Response matches every tool fact."}
    return {
        "score": score,
        "explanation": "Response fails to: " + "; ".join(dict.fromkeys(failed)),
    }
