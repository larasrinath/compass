"""Typed, read-only MCP boundary for the dashboard."""

from linkedin_dashboard.mcp.client import MCPClient
from linkedin_dashboard.mcp.envelope import MCPResponseEnvelope
from linkedin_dashboard.mcp.errors import ErrorClass, MCPClientError, MCPErrorDetails
from linkedin_dashboard.mcp.tools import (
    CompanyProfileResult,
    LinkedInReadTools,
    PersonProfileResult,
    SearchPeopleResult,
)

__all__ = [
    "CompanyProfileResult",
    "ErrorClass",
    "LinkedInReadTools",
    "MCPClient",
    "MCPClientError",
    "MCPErrorDetails",
    "MCPResponseEnvelope",
    "PersonProfileResult",
    "SearchPeopleResult",
]
