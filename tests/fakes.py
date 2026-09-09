"""Deterministic stand-ins for the LLM.

Real LLM calls in tests are slow, cost money, and produce different
output run to run, so assertions rot. These fakes let us script exactly
the tool calls a node has to cope with.
"""

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages.utils import convert_to_messages


@dataclass
class FakeResponse:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage_metadata: dict[str, int] | None = None


class FakeLLM:
    """Returns scripted responses, one per invoke()."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.invocations: list[Any] = []
        self.bound_tools: list[Any] = []

    def bind_tools(self, tools: list[Any]) -> "FakeLLM":
        self.bound_tools = tools
        return self

    def invoke(self, messages: Any) -> FakeResponse:
        """Validates its input the way a real provider would.

        A permissive fake hid a crash in the planner's correction path:
        ("tool", "...") tuples are accepted by a list but rejected by
        LangChain, which requires a tool_call_id on every ToolMessage.
        """
        if isinstance(messages, list):
            convert_to_messages(messages)
        self.invocations.append(messages)
        if not self._responses:
            raise AssertionError("FakeLLM ran out of scripted responses")
        return self._responses.pop(0)

    @property
    def invoke_count(self) -> int:
        return len(self.invocations)


def tool_call(name: str, args: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def usage(input_tokens: int = 100, output_tokens: int = 20) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
