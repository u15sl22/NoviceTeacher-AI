import json
import httpx
from pydantic import ValidationError
from .interfaces import SuggestionDraft


class ProviderError(Exception):
    def __init__(self, code, message, raw=None):
        super().__init__(message)
        self.code, self.raw = code, raw


class CompatibleLLMClient:
    """HTTP transport only. No experiment, knowledge, or memory policy here."""
    def __init__(self, base_url, model, api_key, timeout=45, transport=None, extra_body=None):
        self.base_url, self.model, self.api_key = base_url, model, api_key
        self.timeout, self.transport = timeout, transport
        self.extra_body = extra_body or {}

    def complete(self, context):
        if not self.api_key:
            raise ProviderError('MISSING_API_KEY', '真实模型未配置 LLM_API_KEY，请在服务器 .env 中配置后重试。')
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(self.base_url.rstrip('/') + '/chat/completions',
                    headers={'Authorization': f'Bearer {self.api_key}'},
                    json={**self.extra_body, 'model': self.model, 'messages': [
                        {'role': 'system', 'content': context.system},
                        {'role': 'user', 'content': json.dumps(context.user, ensure_ascii=False)}],
                        'response_format': {'type': 'json_object'}})
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError('LLM_TIMEOUT', '模型请求超时，状态已保留，可以重试。') from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f'LLM_HTTP_{exc.response.status_code}', '模型服务拒绝请求，请检查服务器的模型、密钥和额度配置。') from exc
        except (httpx.RequestError, ValueError) as exc:
            raise ProviderError('LLM_NETWORK_OR_RESPONSE', '模型网络或响应异常，请稍后重试。') from exc


class GenericLLMSuggestionProvider:
    def __init__(self, client):
        self.client = client
        self.last_raw = None

    def generate(self, session, round, section, context):
        self.last_raw = self.client.complete(context)
        try:
            choice = self.last_raw['choices'][0]
            if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
                raise ValueError('Incomplete response or refusal')
            parsed = json.loads(choice['message']['content'])
            candidates = parsed['suggestions']
            if not isinstance(candidates, list) or len(candidates) > 2:
                raise ValueError('Expected 0–2 suggestions')
            return [SuggestionDraft.model_validate(item) for item in candidates]
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ProviderError('MALFORMED_LLM_OUTPUT', '模型返回格式不符合 0–2 条建议约定，未应用任何修改，请重试。', self.last_raw) from exc
