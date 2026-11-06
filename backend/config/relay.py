"""Read only the explicitly selected relay configuration; never execute it."""

import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import SecretStr


@dataclass(frozen=True)
class RelayConfig:
    endpoint: str
    model: str
    effort: str
    key: SecretStr


def read_config(path: Path) -> RelayConfig:
    """Parse only the user-selected data file. Never execute it or echo it."""
    text = path.read_text(encoding="utf-8-sig")

    def field(name):
        match = re.search(r"^\s*" + name + r'\s*=\s*"([^"\r\n]+)"', text, re.M)
        if match is None:
            raise ValueError("relay_configuration_missing_field:" + name)
        return match.group(1)

    keys = set(re.findall(r"\bsk-[A-Za-z0-9_-]+", text))
    if len(keys) != 1:
        raise ValueError("relay_configuration_key_missing_or_ambiguous")
    url = httpx.URL(field("base_url").rstrip("/") + "/responses")
    if (
        url.scheme != "https"
        or not url.host
        or url.username
        or url.password
        or url.query
        or url.fragment
        or field("wire_api") != "responses"
    ):
        raise ValueError("relay_configuration_endpoint_invalid")
    return RelayConfig(
        endpoint=str(url),
        model=field("model"),
        effort=field("model_reasoning_effort"),
        key=SecretStr(keys.pop()),
    )
