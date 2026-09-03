"""Provider-neutral proposal interface. Null through M5."""

from linkedin_dashboard.llm.null import NullProvider
from linkedin_dashboard.llm.protocol import LLMProvider

__all__ = ["LLMProvider", "NullProvider"]
