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
    llm_provider: str = 'deepseek'
    auth_provider: str = 'development'
    development_username: str = 'development'
    context_contributors: list[str] = Field(default_factory=lambda: [
        'lesson_context', 'session_memory', 'knowledge_retrieval', 'case_retrieval'])
    max_context_tokens: int = Field(default=12000, ge=256)
    memory_provider: str = 'rule_based'
    case_retriever: str = 'metadata'
    knowledge_retriever: str = 'metadata'
    retrieval_top_k: int = Field(default=3, ge=1, le=10)
    section_parser: str = 'bounded_v2'
    revision_strategy: str = 'replace_or_append_v2'


settings = Settings()
