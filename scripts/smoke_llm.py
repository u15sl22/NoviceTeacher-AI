"""Explicit real-service probe. Sends only the bundled sample, never prints secrets."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.settings import settings
from app.providers.config import ExperimentConfigFactory
from app.providers.llm import ProviderError


class EmptyHistory:
    def rejected_suggestions(self, *args): return []


if not settings.llm_api_key:
    print('NOT RUN: Configure LLM_API_KEY in the root .env first.')
    raise SystemExit(2)
settings.suggestion_provider = 'generic_llm'
factory = ExperimentConfigFactory(settings)
cfg = factory.build(factory.snapshot(), EmptyHistory())
section = {'id': 'smoke', 'title': '新知探究', 'section_type': 'exploration',
           'current_content': '学生将圆形纸片对折，涂出其中一份。教师讲解二分之一的分子和分母。'}
metadata = {'subject': '数学', 'grade': '三年级', 'topic': '分数的初步认识'}
ctx = cfg.context_builder.build(section, metadata,
    cfg.memory_provider.build_memory({'id': 'smoke'}, {}, section), [])
try:
    suggestions = cfg.suggestion_provider.generate({}, {}, section, ctx)
    print(f'PASS: real service returned {len(suggestions)} validated suggestions.')
    for suggestion in suggestions: print(suggestion.model_dump_json())
except ProviderError as error:
    print(f'FAIL: {error.code}: {error}')
    raise SystemExit(1)
