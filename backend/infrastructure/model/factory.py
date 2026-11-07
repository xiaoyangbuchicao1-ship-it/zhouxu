"""Open one of the configured model deployments for a Harness role."""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from backend.config.relay import read_config
from backend.config.settings import Settings, load_key
from backend.contracts.modules.loop import ModelChoice
from backend.contracts.schemas.core import PortError
from backend.infrastructure.model.deepseek import DeepSeekModel
from backend.infrastructure.model.responses import ResponsesModel, ResponsesTransport


def _resolve_path(settings: Settings, value: str) -> Path:
    """Resolve provider files relative to the backend config root."""
    path = Path(value)
    if path.is_absolute():
        return path
    from backend.config.settings import BACKEND_ROOT

    return BACKEND_ROOT / path


def select_model_settings(
    settings: Settings, model_id: ModelChoice | str | None
) -> Settings:
    """Return a copy of settings bound to one public model choice."""
    choice = model_id or "deepseek-v4"
    deployment = settings.model_deployments.get(choice)
    if deployment is None:
        raise PortError(settings.error("model_profile_invalid"))

    selected = settings.model_copy(deep=True)
    selected.model.provider = deployment.provider
    selected.model.name = deployment.name
    selected.model.endpoint = deployment.endpoint
    selected.model.allowed_hosts = list(deployment.allowed_hosts)
    selected.model.credential_file = deployment.credential_file
    selected.model.credential_names = list(deployment.credential_names)

    if deployment.provider == "relay":
        try:
            relay = read_config(_resolve_path(settings, deployment.credential_file))
        except (OSError, ValueError) as exc:
            raise PortError(settings.error("credential_missing")) from exc
        selected.model.endpoint = relay.endpoint
        selected.model.name = relay.model
        selected.model.allowed_hosts = [httpx.URL(relay.endpoint).host]
        if relay.model not in selected.model_capabilities:
            raise PortError(settings.error("model_profile_invalid"))
    elif not selected.model.endpoint or not selected.model.allowed_hosts:
        raise PortError(settings.error("model_profile_invalid"))

    return selected


@asynccontextmanager
async def open_model(*, settings: Settings, events, role: str, collect_metrics=False):
    if role not in {"main", "review"}:
        raise ValueError("unknown_model_role")
    if settings.model_routing is not None or settings.model.thinking_mode not in settings.model.modes:
        raise PortError(settings.error("model_profile_invalid"))
    selected = settings.model_copy(deep=True)
    if role == "review":
        selected.model.thinking_mode = "none"
        selected.model.decision_protocol = "text"
    if selected.model.provider == "relay":
        try:
            relay = read_config(_resolve_path(selected, selected.model.credential_file))
        except (OSError, ValueError) as exc:
            raise PortError(settings.error("credential_missing")) from exc
        # Re-derive connection data from the protected file so a stale TOML
        # deployment entry cannot redirect the authenticated client.
        selected.model.endpoint = relay.endpoint
        selected.model.name = relay.model
        selected.model.allowed_hosts = [httpx.URL(relay.endpoint).host]
        transport = ResponsesTransport(
            relay,
            max_cached_runs=4,
            collect_metrics=collect_metrics,
        )
        model = ResponsesModel(selected, relay.key, events, transport=transport)
    else:
        model = DeepSeekModel(selected, load_key(selected), events)
    async with model:
        yield model
