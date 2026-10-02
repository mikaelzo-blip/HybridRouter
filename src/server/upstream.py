import copy
import logging
from typing import Any
import httpx
from src.schemas import FullRouterConfigFile

logger = logging.getLogger("hybrid_router.upstream")


class UpstreamClient:
    def __init__(self, config: FullRouterConfigFile):
        self.config = config
        antigravity = config.providers.get("antigravity")
        self.base_url = antigravity.base_url.rstrip("/") if antigravity else "http://127.0.0.1:20128/v1"
        self.api_key = antigravity.api_key if antigravity else ""
        self.fallback_chain = config.resilience.fallback_chain
        self.client = httpx.AsyncClient(timeout=180.0)

    def resolve_model_id(self, alias: str) -> str:
        model_cfg = self.config.models.get(alias)
        if model_cfg and model_cfg.model:
            return model_cfg.model
        return alias

    def prepare_payload(self, alias: str, payload: dict[str, Any]) -> dict[str, Any]:
        prepared = copy.deepcopy(payload)
        model_id = self.resolve_model_id(alias)
        prepared["model"] = model_id

        # Clean custom hybrid metadata if present so upstream doesn't reject
        prepared.pop("metadata", None)

        model_cfg = self.config.models.get(alias)
        if model_cfg and model_cfg.parameters:
            params = model_cfg.parameters
            if params.max_tokens is not None and "max_tokens" not in prepared:
                prepared["max_tokens"] = params.max_tokens
            if params.temperature is not None and "temperature" not in prepared:
                prepared["temperature"] = params.temperature
            # Thinking parameters
            if params.thinking and params.thinking.type == "enabled":
                if "thinking" not in prepared:
                    prepared["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": params.thinking.budget_tokens
                    }

        return prepared

    async def forward_request(self, alias: str, payload: dict[str, Any]) -> tuple[httpx.Response, str]:
        current_alias = alias
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/chat/completions"
        req_payload = copy.deepcopy(payload)
        req_payload["stream"] = False

        while True:
            outbound_payload = self.prepare_payload(current_alias, req_payload)
            logger.info("Forwarding request to %s (alias: %s)", outbound_payload["model"], current_alias)

            try:
                resp = await self.client.post(url, json=outbound_payload, headers=headers)
            except Exception as e:
                logger.warning("Upstream call failed on alias %s: %s", current_alias, e)
                # Check fallback
                if current_alias in self.fallback_chain:
                    next_alias = self.fallback_chain[current_alias]
                    logger.info("Executing fallback from %s -> %s", current_alias, next_alias)
                    current_alias = next_alias
                    continue
                raise

            # Check transient statuses: 429, 502, 503, 504
            if resp.status_code in (429, 502, 503, 504):
                if current_alias in self.fallback_chain:
                    next_alias = self.fallback_chain[current_alias]
                    logger.warning(
                        "Upstream returned %d for %s. Falling back to %s",
                        resp.status_code, current_alias, next_alias
                    )
                    current_alias = next_alias
                    continue

            return resp, current_alias

    async def forward_stream(self, alias: str, payload: dict[str, Any]) -> tuple[Any, str, dict[str, str]]:
        current_alias = alias
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/chat/completions"
        req_payload = copy.deepcopy(payload)
        req_payload["stream"] = True

        while True:
            outbound_payload = self.prepare_payload(current_alias, req_payload)
            logger.info("Forwarding stream to %s (alias: %s)", outbound_payload["model"], current_alias)

            stream_cm = self.client.stream("POST", url, json=outbound_payload, headers=headers)
            try:
                resp = await stream_cm.__aenter__()
            except Exception as e:
                logger.warning("Upstream stream initiation failed on alias %s: %s", current_alias, e)
                if current_alias in self.fallback_chain:
                    next_alias = self.fallback_chain[current_alias]
                    current_alias = next_alias
                    continue
                raise

            if resp.status_code in (429, 502, 503, 504):
                await stream_cm.__aexit__(None, None, None)
                if current_alias in self.fallback_chain:
                    next_alias = self.fallback_chain[current_alias]
                    logger.warning(
                        "Upstream stream returned %d for %s. Falling back to %s",
                        resp.status_code, current_alias, next_alias
                    )
                    current_alias = next_alias
                    continue

            async def chunk_generator():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                finally:
                    await stream_cm.__aexit__(None, None, None)

            return chunk_generator(), current_alias, dict(resp.headers)

    async def close(self) -> None:
        await self.client.aclose()
