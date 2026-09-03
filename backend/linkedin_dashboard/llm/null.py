from __future__ import annotations

from linkedin_dashboard.parsing.verify import SpanProposal


class NullProvider:
    async def propose_fields(
        self, section_name: str, raw_text: str
    ) -> tuple[SpanProposal, ...]:
        del section_name, raw_text
        return ()
