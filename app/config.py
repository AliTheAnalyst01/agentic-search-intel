from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    azure_inference_endpoint: str
    azure_inference_credential: str
    azure_model: str = "gpt-4.1-mini"

    # DataForSEO
    dataforseo_mode: Literal["live", "sandbox", "mock"] = "mock"
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    dataforseo_base_url: str = "https://sandbox.dataforseo.com"
    dataforseo_timeout: float = 20.0
    dataforseo_max_attempts: int = 3


settings = Settings()