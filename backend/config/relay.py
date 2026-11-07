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

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    def field(name):
        match = re.search(r"^\s*" + name + r'\s*=\s*"([^"\r\n]+)"', text, re.M)
        if match is not None:
            return match.group(1)
        # The compact handoff file used by the local product is intentionally
        # data-only: model ID, one secret, and the relay base URL on separate
        # lines. Keep accepting the documented TOML form above for benchmarks.
        if len(lines) >= 3:
            if name == "model":
                return lines[0]
            if name == "model_reasoning_effort":
                return "low"
            if name == "base_url":
                return lines[-1]
            if name == "wire_api":
                return "responses"
        raise ValueError("relay_configuration_missing_field:" + name)

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
