from __future__ import annotations

import httpx

from ..config import Settings
from ..policy import ProviderSpec
from .base import Provider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider


class ProviderRegistry:
    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._cache: dict[tuple, Provider] = {}

    def get(self, name: str, spec: ProviderSpec) -> Provider:
        key = (name, spec.kind, spec.base_url, spec.api_key_env)
        prov = self._cache.get(key)
        if prov is None:
            if spec.kind == "mock":
                prov = MockProvider(self._settings.mock_latency_scale)
            else:
                assert spec.base_url, f"provider {name} needs base_url"
                prov = OpenAICompatProvider(name, spec.base_url, spec.api_key_env, self._client)
            self._cache[key] = prov
        return prov
