import os
import re
from typing import Any
import yaml
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from src.schemas import FullRouterConfigFile

load_dotenv()

PLACEHOLDER_REGEX = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def _replace_placeholder_in_str(value: str, unresolved: set[str]) -> str:
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        val = os.environ.get(var_name)
        if val is not None:
            return val
        unresolved.add(var_name)
        return match.group(0)

    return PLACEHOLDER_REGEX.sub(replacer, value)


def resolve_env_placeholders(data: Any, unresolved: set[str] | None = None) -> tuple[Any, set[str]]:
    if unresolved is None:
        unresolved = set()

    if isinstance(data, dict):
        resolved_dict = {}
        for k, v in data.items():
            resolved_dict[k], _ = resolve_env_placeholders(v, unresolved)
        return resolved_dict, unresolved
    elif isinstance(data, list):
        resolved_list = []
        for item in data:
            res_item, _ = resolve_env_placeholders(item, unresolved)
            resolved_list.append(res_item)
        return resolved_list, unresolved
    elif isinstance(data, str):
        return _replace_placeholder_in_str(data, unresolved), unresolved
    else:
        return data, unresolved


def load_router_config(file_path: str) -> FullRouterConfigFile:
    with open(file_path, "r", encoding="utf-8") as f:
        raw_yaml = yaml.safe_load(f) or {}

    resolved_data, unresolved = resolve_env_placeholders(raw_yaml)
    return FullRouterConfigFile.model_validate(resolved_data)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    port: int = Field(default=20250, alias="PORT")
    host: str = Field(default="127.0.0.1", alias="HOST")
    upstream_base_url: str = Field(default="http://127.0.0.1:20128/v1", alias="UPSTREAM_BASE_URL")
    routing_config_path: str = Field(default="config/9router-production.yaml", alias="ROUTING_CONFIG_PATH")
    log_level: str = Field(default="info", alias="LOG_LEVEL")
    circuit_state_file: str | None = Field(default=None, alias="CIRCUIT_STATE_FILE")
    spend_state_file: str | None = Field(default=None, alias="SPEND_STATE_FILE")
