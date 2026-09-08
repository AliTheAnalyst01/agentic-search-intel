from pydantic import BaseModel, Field
from app.llm import get_llm


class SerpLookup(BaseModel):
    """Look up organic search results for a keyword."""
    keyword: str = Field(description="The search keyword")
    location: str = Field(description="Country name, e.g. United Kingdom")


llm = get_llm()

r1 = llm.invoke("Reply with exactly: connection ok")
print("text:  ", r1.content)
print("tokens:", r1.usage_metadata)

r2 = llm.bind_tools([SerpLookup]).invoke(
    "Check how surferseo.com ranks for 'best seo tool' in the UK."
)
print("calls: ", r2.tool_calls)
