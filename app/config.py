from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_inference_endpoint: str
    azure_inference_credential: str
    azure_model: str = "gpt-4.1-mini"


settings = Settings()