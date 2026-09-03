from __future__ import annotations

from typing import Protocol

from linkedin_dashboard.parsing.verify import SpanProposal


class LLMProvider(Protocol):
    """Providers propose text spans; only local verification can approve them."""

    async def propose_fields(
        self, section_name: str, raw_text: str
    ) -> tuple[SpanProposal, ...]: ...
