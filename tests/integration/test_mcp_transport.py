from __future__ import annotations

import asyncio
import socket
from contextlib import suppress

import pytest
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from linkedin_dashboard.mcp.client import MCPClient
from linkedin_dashboard.mcp.tools import LinkedInReadTools


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.mark.asyncio
async def test_real_fastmcp_streamable_http_connect_list_and_call() -> None:
    mcp = FastMCP("synthetic-linkedin")

    @mcp.tool
    def get_person_profile(linkedin_username: str) -> dict[str, object]:
        """Return a synthetic profile and the request headers it observed."""
        request = get_http_request()
        return {
            "url": f"https://www.linkedin.com/in/{linkedin_username}/",
            "sections": {"main_profile": f"{linkedin_username}\nEngineer"},
            "observed_headers": dict(request.headers),
            "synthetic_extension": {"preserved": True},
        }

    port = _free_port()
    app = mcp.http_app(path="/mcp", stateless_http=True)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="critical",
            access_log=False,
            timeout_graceful_shutdown=0,
        )
    )
    server_task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        if server_task.done():
            await server_task
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("synthetic FastMCP server did not start")

    try:
        client = MCPClient(f"http://127.0.0.1:{port}/mcp")
        listed = await client.list_tools()
        result = await LinkedInReadTools(client).get_person_profile("alice")
    finally:
        server.should_exit = True
        with suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(server_task, timeout=2)

    assert [tool.name for tool in listed] == ["get_person_profile"]
    assert result.payload is not None
    assert result.payload.sections == {"main_profile": "alice\nEngineer"}
    assert result.payload.model_extra is not None
    headers = result.payload.model_extra["observed_headers"]
    assert isinstance(headers, dict)
    assert headers["host"] == f"127.0.0.1:{port}"
    lowered = {str(name).casefold() for name in headers}
    assert not lowered.intersection(
        {
            "authorization",
            "cookie",
            "forwarded",
            "origin",
            "proxy-authorization",
        }
    )
    assert not any(name.startswith("x-forwarded-") for name in lowered)
    assert result.response.structured_content is not None
    assert result.response.structured_content["synthetic_extension"] == {
        "preserved": True
    }
