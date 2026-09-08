from langchain_openai import ChatOpenAI

from app.config import settings


def get_llm(model: str | None = None, temperature: float = 0.0) -> ChatOpenAI:
    """Single place the whole app gets its model from."""
    return ChatOpenAI(
        base_url=settings.azure_inference_endpoint,
        api_key=settings.azure_inference_credential,
        model=model or settings.azure_model,
        temperature=temperature,
        timeout=30,
        max_retries=0,
    )