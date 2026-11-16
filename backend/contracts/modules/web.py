"""Contracts for the read-only DeepSeek Search and Fetch tools."""

from pydantic import Field

from backend.contracts.schemas.core import Contract


class WebSearchInput(Contract):
    """A bounded discovery query.

    Search deliberately does not expose ``need_content``.  The model must use
    ``web_read`` before treating a search hit as readable source material.
    """

    query: str = Field(min_length=1, max_length=128)
    count: int = Field(default=10, ge=1, le=50)


class WebReadInput(Contract):
    """A public HTTP(S) URL returned by ``web_search`` or supplied by a user."""

    url: str = Field(min_length=8, max_length=8192)
    max_chars: int | None = Field(default=None, ge=256)
