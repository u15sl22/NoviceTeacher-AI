from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./pedago_loop.db'
    suggestion_provider: str = 'generic_llm'
    llm_base_url: str = 'https://api.deepseek.com'
    llm_model: str = 'deepseek-flash'
    llm_api_key: str = ''
    llm_timeout_seconds: float = 45
    llm_extra_body: dict = Field(default_factory=dict)


settings = Settings()
