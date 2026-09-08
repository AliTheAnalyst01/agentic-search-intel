"""Tool argument schemas.

The LLM chooses intent (which keyword, which platform, which tool).
Code supplies mechanism (depth, device, credentials, result limits).
Constrained vocabularies use Literal so an invalid value is rejected
before any request is constructed.

extra="forbid" everywhere: a hallucinated parameter is a validation
failure the planner can be told about, not a silently ignored field.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# A curated subset. In production this would be loaded from
# DataForSEO's locations endpoint and cached.
Location = Literal[
    "United Kingdom",
    "United States",
    "Canada",
    "Australia",
    "Germany",
]

Platform = Literal["chat_gpt", "gemini", "claude", "perplexity"]

MAX_KEYWORDS = 5


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SerpOrganicArgs(_Base):
    """Fetch Google organic search results for one keyword."""

    keyword: str = Field(
        min_length=2,
        max_length=200,
        description="The exact search query to look up, e.g. 'best seo tool'",
    )
    location: Location = Field(
        description="Country to search from. Must be one of the allowed values."
    )

    @field_validator("keyword")
    @classmethod
    def reject_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("keyword cannot be blank")
        return v


class LLMVisibilityArgs(_Base):
    """Ask an AI platform a question and capture its answer and citations."""

    prompt: str = Field(
        min_length=5,
        max_length=500,
        description=(
            "The question to put to the AI platform, phrased as a real user "
            "would, e.g. 'What are the best SEO tools for content teams?'"
        ),
    )
    platform: Platform = Field(
        description="Which AI platform to query. Must be one of the allowed values."
    )


class KeywordMetricsArgs(_Base):
    """Fetch search volume and difficulty for a small batch of keywords."""

    keywords: list[str] = Field(
        min_length=1,
        max_length=MAX_KEYWORDS,
        description=f"Between 1 and {MAX_KEYWORDS} keywords to look up metrics for",
    )
    location: Location = Field(description="Country the metrics should reflect")

    @field_validator("keywords")
    @classmethod
    def reject_blanks_and_dupes(cls, v: list[str]) -> list[str]:
        cleaned = [k.strip() for k in v if k.strip()]
        if not cleaned:
            raise ValueError("keywords cannot be empty or all blank")
        seen: list[str] = []
        for k in cleaned:
            if k.lower() not in {s.lower() for s in seen}:
                seen.append(k)
        return seen


TOOL_SCHEMAS: dict[str, type[_Base]] = {
    "serp_organic_lookup": SerpOrganicArgs,
    "llm_visibility_lookup": LLMVisibilityArgs,
    "keyword_metrics_lookup": KeywordMetricsArgs,
}


@dataclass(frozen=True)
class ToolCallResult:
    """Outcome of validating one LLM tool call.

    Returned rather than raised so a node can route on failure instead
    of crashing, and can feed the error text back to the planner.
    """

    ok: bool
    tool_name: str
    args: _Base | None = None
    errors: tuple[str, ...] = ()


def validate_tool_call(tool_name: str, raw_args: object) -> ToolCallResult:
    """Validate an LLM-produced tool call without raising."""
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        known = ", ".join(sorted(TOOL_SCHEMAS))
        return ToolCallResult(
            ok=False,
            tool_name=tool_name,
            errors=(f"Unknown tool '{tool_name}'. Available tools: {known}",),
        )

    if not isinstance(raw_args, dict):
        return ToolCallResult(
            ok=False,
            tool_name=tool_name,
            errors=(f"Arguments must be an object, got {type(raw_args).__name__}",),
        )

    try:
        return ToolCallResult(ok=True, tool_name=tool_name, args=schema(**raw_args))
    except ValidationError as err:
        return ToolCallResult(
            ok=False, tool_name=tool_name, errors=tuple(_format_errors(err))
        )


def _format_errors(err: ValidationError) -> list[str]:
    """Human-readable messages, suitable for feeding back to the LLM."""
    out = []
    for e in err.errors():
        field = ".".join(str(p) for p in e["loc"]) or "(root)"
        out.append(f"{field}: {e['msg']}")
    return out
