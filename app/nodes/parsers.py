"""Parse DataForSEO response shapes into fragments.

Every access is defensive: real responses omit fields, return empty
results, or nest differently by endpoint. A parser returns what it
found and never raises on a shape it doesn't recognise.
"""

from dataclasses import dataclass, field
from typing import Any


def _tasks(body: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = body.get("tasks")
    return tasks if isinstance(tasks, list) else []


def _first_result(body: dict[str, Any]) -> dict[str, Any]:
    for task in _tasks(body):
        results = task.get("result")
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                return first
    return {}


def _all_results(body: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for task in _tasks(body):
        results = task.get("result")
        if isinstance(results, list):
            out.extend(r for r in results if isinstance(r, dict))
    return out


def normalize_key(text: str) -> str:
    """Merge key: SERP and keyword endpoints disagree on casing."""
    return " ".join(text.lower().split())


@dataclass
class SerpFragment:
    keyword: str
    domain_visible: bool
    visibility_position: int | None
    results_seen: int


@dataclass
class KeywordFragment:
    keyword: str
    search_volume: int
    difficulty: int


@dataclass
class AIFragment:
    prompt: str
    platform: str
    brand_mentioned: bool
    citations: list[str] = field(default_factory=list)


def parse_serp(body: dict[str, Any], domain: str) -> SerpFragment | None:
    """Whether the domain appears in organic results, and at what rank."""
    result = _first_result(body)
    if not result:
        return None

    keyword = str(result.get("keyword", ""))
    items = result.get("items")
    items = items if isinstance(items, list) else []

    target = domain.lower().removeprefix("www.")
    position: int | None = None

    for item in items:
        if not isinstance(item, dict) or item.get("type") != "organic":
            continue
        item_domain = str(item.get("domain", "")).lower().removeprefix("www.")
        if item_domain == target:
            rank = item.get("rank_absolute") or item.get("rank_group")
            if isinstance(rank, int):
                position = rank
                break

    return SerpFragment(
        keyword=keyword,
        domain_visible=position is not None,
        visibility_position=position,
        results_seen=len(items),
    )


def parse_keyword_metrics(body: dict[str, Any]) -> list[KeywordFragment]:
    """Volume and difficulty, one fragment per keyword in the batch."""
    fragments: list[KeywordFragment] = []

    for result in _all_results(body):
        keyword = str(result.get("keyword", ""))
        if not keyword:
            continue

        info = result.get("keyword_info") or {}
        props = result.get("keyword_properties") or {}

        volume = info.get("search_volume")
        difficulty = props.get("keyword_difficulty")

        fragments.append(
            KeywordFragment(
                keyword=keyword,
                search_volume=int(volume) if isinstance(volume, (int, float)) else 0,
                difficulty=int(difficulty) if isinstance(difficulty, (int, float)) else 0,
            )
        )

    return fragments


def parse_ai_response(
    body: dict[str, Any], brand: str, domain: str
) -> AIFragment | None:
    """Whether the brand is named or cited in an AI platform's answer."""
    result = _first_result(body)
    if not result:
        return None

    platform = str(result.get("model_name", ""))
    prompt = ""
    for task in _tasks(body):
        data = task.get("data") or {}
        prompt = str(data.get("user_prompt", "")) or prompt

    text_parts: list[str] = []
    citations: list[str] = []

    items = result.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        for section in item.get("sections") or []:
            if not isinstance(section, dict):
                continue
            text_parts.append(str(section.get("text", "")))
            for note in section.get("annotations") or []:
                if isinstance(note, dict) and note.get("url"):
                    citations.append(str(note["url"]))

    blob = " ".join(text_parts).lower()
    target = domain.lower().removeprefix("www.")
    mentioned = (
        brand.lower() in blob
        or target in blob
        or any(target in c.lower() for c in citations)
    )

    return AIFragment(
        prompt=prompt,
        platform=platform,
        brand_mentioned=mentioned,
        citations=citations,
    )
