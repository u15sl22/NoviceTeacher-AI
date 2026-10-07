from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.engine import URL
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProfile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,49}$')
    label: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=60)
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=100)
    timeout: float | None = Field(default=None, ge=5, le=300)
    extra_body: dict = Field(default_factory=dict)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./pedago_loop.db'
    database_host: str | None = None
    database_port: int = 5432
    database_name: str = 'pedago_loop'
    database_user: str = 'pedago'
    database_password: str = ''
    storage_root: Path = Path(__file__).resolve().parents[2] / 'storage' / 'documents'
    max_document_bytes: int = Field(default=15 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)

    @model_validator(mode='after')
    def resolve_database(self):
        if self.database_host:
            self.database_url = URL.create('postgresql+psycopg', username=self.database_user,
                password=self.database_password, host=self.database_host, port=self.database_port,
                database=self.database_name).render_as_string(hide_password=False)
        return self
    suggestion_provider: str = 'generic_llm'
    llm_base_url: str = 'https://api.deepseek.com'
    llm_model: str = 'deepseek-flash'
    llm_api_key: str = ''
    llm_timeout_seconds: float = 45
    llm_extra_body: dict = Field(default_factory=dict)
    llm_provider: str = 'deepseek'
    llm_profiles: list[LLMProfile] = Field(default_factory=list)
    llm_api_keys: dict[str, str] = Field(default_factory=dict)
    default_llm_profile: str | None = None
    zhipu_api_key: str = ''
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

    def model_profiles(self):
        if self.suggestion_provider == 'mock':
            return [LLMProfile(id='mock', label='模拟演示', provider='mock',
                base_url='https://mock.invalid', model='mock')]
        profiles = self.llm_profiles or [LLMProfile(id=self.llm_provider, label=self.llm_provider.title(),
            provider=self.llm_provider, base_url=self.llm_base_url, model=self.llm_model,
            timeout=self.llm_timeout_seconds, extra_body=self.llm_extra_body)]
        identifiers = [profile.id for profile in profiles]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('LLM profile IDs must be unique')
        default = self.default_llm_profile or profiles[0].id
        if default not in identifiers:
            raise ValueError('DEFAULT_LLM_PROFILE is not present in LLM_PROFILES')
        return profiles

    def default_profile_id(self):
        profiles = self.model_profiles()
        if self.suggestion_provider == 'mock':
            return profiles[0].id
        return self.default_llm_profile or profiles[0].id

    def resolve_profile(self, profile_id=None):
        selected = profile_id or self.default_profile_id()
        profile = next((item for item in self.model_profiles() if item.id == selected), None)
        if not profile:
            raise ValueError('Unknown model profile: ' + selected)
        return profile

    def api_key_for(self, profile_id, snapshot=None):
        if self.suggestion_provider == 'mock' or profile_id == 'mock':
            return 'mock'
        if key := self.llm_api_keys.get(profile_id, '').strip():
            return key
        profile = next((item for item in self.model_profiles() if item.id == profile_id), None)
        if profile and profile.provider == 'zhipu' and self.zhipu_api_key.strip():
            return self.zhipu_api_key.strip()
        if profile and profile.provider == self.llm_provider and (
                profile.base_url.rstrip('/') == self.llm_base_url.rstrip('/') or profile_id == self.default_profile_id()):
            return self.llm_api_key.strip()
        if snapshot and snapshot.get('model_provider') == self.llm_provider and not snapshot.get('model_profile'):
            return self.llm_api_key.strip()
        return ''


settings = Settings()
