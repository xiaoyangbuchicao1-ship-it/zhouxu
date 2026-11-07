"""Public model choices stay provider-neutral at the HTTP boundary."""

from pathlib import Path

import pytest

from backend.config.relay import read_config
from backend.config.settings import load_settings
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.factory import select_model_settings


def test_compact_relay_document_is_parsed_without_secret_in_repr(tmp_path: Path):
    path = tmp_path / "relay.txt"
    path.write_text(
        "gpt-6.1-sol\n"
        "sk-synthetic-model-selection\n"
        "https://relay.example.test/v1\n",
        encoding="utf-8",
    )

    value = read_config(path)

    assert value.model == "gpt-6.1-sol"
    assert value.endpoint == "https://relay.example.test/v1/responses"
    assert value.effort == "low"
    assert "sk-synthetic-model-selection" not in repr(value)


def test_public_model_choices_resolve_to_configured_deployments():
    settings = load_settings()

    deepseek = select_model_settings(settings, "deepseek-v4")
    relay = select_model_settings(settings, "gpt")

    assert deepseek.model.provider == "deepseek"
    assert deepseek.model.name == "deepseek-flash"
    assert relay.model.provider == "relay"
    assert relay.model.name == "gpt-6.1-sol"
    assert relay.model.endpoint.endswith("/v1/responses")
    assert len(relay.model.allowed_hosts) == 1
    assert relay.model.allowed_hosts[0] in relay.model.endpoint


def test_unknown_public_model_choice_fails_closed():
    with pytest.raises(PortError) as caught:
        select_model_settings(load_settings(), "untrusted-model")

    assert caught.value.error.code == "model_profile_invalid"
