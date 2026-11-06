"""Compatibility imports for the earlier evaluation; implementation is now shared."""

from backend.config.relay import RelayConfig as RelayConfig
from backend.config.relay import read_config as read_config
from backend.infrastructure.model.responses import (
    ResponsesTransport as _ResponsesTransport,
)


class ResponsesTransport(_ResponsesTransport):
    def __init__(self, config, *, transport=None):
        super().__init__(config, transport=transport, collect_metrics=True)
