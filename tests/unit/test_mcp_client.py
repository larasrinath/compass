from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self

import httpx
import pytest
from linkedin_dashboard.mcp import client as client_module
from linkedin_dashboard.mcp.client import MCPClient
from linkedin_dashboard.mcp.errors import ErrorClass, MCPClientError


def _response(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "content": [{"type": "text", "text": '{"url":"u","sections":{}}'}],
        "structuredContent": {"url": "u", "sections": {}},
        "isError": False,
    }
    value.update(overrides)
    return value


class FakeSession:
    def __init__(
        self,
        *,
        result: object | BaseException | None = None,
        result_factory: Callable[[], Awaitable[object]] | None = None,
        tools: list[object] | None = None,
    ) -> None:
        self.result = result if result is not None else _response()
        self.result_factory = result_factory
        self.tools = tools if tools is not None else [{"name": "search_people"}]
        self.entered = 0
        self.exited = 0
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def __aenter__(self) -> Self:
        self.entered += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.exited += 1

    async def list_tools(self) -> list[object]:
        return self.tools

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        progress_handler: Callable[..., Awaitable[None]] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - fake mirrors FastMCP
    ) -> object:
        if progress_handler is not None:
            await progress_handler(1.0, 2.0, "private server text")
        self.calls.append((name, arguments, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        if self.result_factory is not None:
            return await self.result_factory()
        return self.result


class RecordingFactory:
    def __init__(self, builder: Callable[[], FakeSession] | None = None) -> None:
        self.builder = builder or FakeSession
        self.arguments: list[tuple[str, float]] = []
        self.sessions: list[FakeSession] = []

    def __call__(self, url: str, timeout: float) -> FakeSession:
        self.arguments.append((url, timeout))
        session = self.builder()
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_each_operation_uses_a_fresh_client_and_240_second_timeout() -> None:
    factory = RecordingFactory()
    client = MCPClient("http://127.0.0.1:8000/mcp", client_factory=factory)

    await client.list_tools()
    await client.call_tool("search_people", {"keywords": "python"})

    assert factory.arguments == [
        ("http://127.0.0.1:8000/mcp", 240.0),
        ("http://127.0.0.1:8000/mcp", 240.0),
    ]
    assert len(factory.sessions) == 2
    assert all(session.entered == session.exited == 1 for session in factory.sessions)
    assert factory.sessions[1].calls == [
        ("search_people", {"keywords": "python"}, 240.0)
    ]


@pytest.mark.asyncio
async def test_timeout_is_classified_closed_and_never_retried() -> None:
    factory = RecordingFactory(lambda: FakeSession(result=TimeoutError("late secret")))
    client = MCPClient("http://127.0.0.1:8000/mcp", client_factory=factory)

    with pytest.raises(MCPClientError) as caught:
        await client.call_tool("search_people", {"keywords": "python"})

    assert caught.value.details.error_class is ErrorClass.TIMEOUT
    assert "secret" not in str(caught.value)
    assert len(factory.sessions) == 1
    assert len(factory.sessions[0].calls) == 1
    assert factory.sessions[0].exited == 1


@pytest.mark.asyncio
async def test_cancellation_closes_client_and_is_not_reclassified_or_retried() -> None:
    started = asyncio.Event()

    async def wait_forever() -> object:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    factory = RecordingFactory(lambda: FakeSession(result_factory=wait_forever))
    client = MCPClient("http://127.0.0.1:8000/mcp", client_factory=factory)
    task = asyncio.create_task(client.call_tool("search_people", {"keywords": "x"}))
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(factory.sessions) == 1
    assert len(factory.sessions[0].calls) == 1
    assert factory.sessions[0].exited == 1


@pytest.mark.asyncio
async def test_malformed_response_keeps_only_safe_partial_diagnostics() -> None:
    malformed = {
        "content": "not-a-list",
        "structuredContent": {
            "section_errors": {
                "experience": {
                    "error_type": "Broken",
                    "runtime": {"source_profile_dir": "/Users/operator/.linkedin-mcp"},
                }
            }
        },
        "isError": False,
    }
    factory = RecordingFactory(lambda: FakeSession(result=malformed))
    client = MCPClient("http://127.0.0.1:8000/mcp", client_factory=factory)

    with pytest.raises(MCPClientError) as caught:
        await client.call_tool("get_person_profile", {"linkedin_username": "alice"})

    details = caught.value.details.model_dump(mode="json")
    assert details["error_class"] == "UNKNOWN"
    assert "runtime" not in str(details).casefold()
    assert ".linkedin-mcp" not in str(details)


@pytest.mark.asyncio
async def test_raw_response_is_captured_before_malformed_envelope_parsing() -> None:
    malformed = {
        "content": "not-a-list",
        "structuredContent": {"url": "u", "unknown": {"kept": True}},
        "isError": False,
    }
    factory = RecordingFactory(lambda: FakeSession(result=malformed))
    captured: list[dict[str, Any]] = []
    progress: list[tuple[float, float | None]] = []
    client = MCPClient("http://127.0.0.1:8000/mcp", client_factory=factory)

    with pytest.raises(MCPClientError):
        await client.call_tool(
            "get_person_profile",
            {"linkedin_username": "alice"},
            raw_response_capture=lambda raw: _append(captured, raw),
            progress_capture=lambda current, total, _message: _append(
                progress, (current, total)
            ),
        )

    assert captured == [malformed]
    assert progress == [(1.0, 2.0)]


async def _append(target: list[Any], value: Any) -> None:
    target.append(value)


@pytest.mark.asyncio
async def test_response_size_is_bounded_without_echoing_the_body(monkeypatch) -> None:
    factory = RecordingFactory(
        lambda: FakeSession(
            result=_response(structuredContent={"url": "u", "sections": {"x": "large"}})
        )
    )
    monkeypatch.setattr(client_module, "MAX_MCP_RESPONSE_BYTES", 10)
    client = MCPClient("http://127.0.0.1:8000/mcp", client_factory=factory)

    with pytest.raises(MCPClientError) as caught:
        await client.call_tool("search_people", {"keywords": "x"})

    assert caught.value.details.partial_payload is None
    assert "large" not in str(caught.value)
    assert len(factory.sessions[0].calls) == 1


@pytest.mark.asyncio
async def test_list_tools_size_is_bounded_without_echoing_descriptions(
    monkeypatch,
) -> None:
    factory = RecordingFactory(
        lambda: FakeSession(
            tools=[{"name": "search_people", "description": "private large text"}]
        )
    )
    monkeypatch.setattr(client_module, "MAX_MCP_RESPONSE_BYTES", 10)
    client = MCPClient("http://127.0.0.1:8000/mcp", client_factory=factory)

    with pytest.raises(MCPClientError) as caught:
        await client.list_tools()

    assert caught.value.details.partial_payload is None
    assert "private" not in str(caught.value)
    assert len(factory.sessions) == 1
    assert factory.sessions[0].entered == factory.sessions[0].exited == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000/mcp",
        "http://localhost:8000/mcp",
        "http://192.0.2.1:8000/mcp",
        "http://user:secret@127.0.0.1:8000/mcp",
        "http://127.0.0.1:8000/other",
        "http://127.0.0.1:8000/mcp?token=secret",
        "http://127.0.0.1:8000/mcp#fragment",
        "http://[::ffff:127.0.0.1]:8000/mcp",
    ],
)
def test_constructor_rejects_non_direct_endpoints(url: str) -> None:
    with pytest.raises(ValueError, match="MCP endpoint"):
        MCPClient(url)


def test_http_factory_disables_redirects_environment_and_auth(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class CapturingClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", CapturingClient)
    timeout = httpx.Timeout(240)
    factory = client_module._DirectHttpxClientFactory("http://127.0.0.1:8000/mcp")
    returned = factory(
        headers={"Accept": "application/json"},
        auth=None,
        timeout=timeout,
        follow_redirects=True,
    )

    assert isinstance(returned, CapturingClient)
    assert captured["headers"] == {"Accept": "application/json"}
    assert captured["auth"] is None
    assert captured["follow_redirects"] is False
    assert captured["timeout"] == timeout
    assert captured["trust_env"] is False
    assert set(captured["event_hooks"]) == {"request"}
    assert len(captured["event_hooks"]["request"]) == 1


@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "Origin",
        "Forwarded",
        "Proxy-Authorization",
        "X-Forwarded-For",
        "X-Forwarded-Host",
        "X-Forwarded-Proto",
        "X-Forwarded-Custom",
        "Cookie",
        "Host",
    ],
)
def test_http_factory_rejects_sensitive_or_forwarding_headers(header: str) -> None:
    factory = client_module._DirectHttpxClientFactory("http://127.0.0.1:8000/mcp")
    with pytest.raises(ValueError, match="forbidden header"):
        factory(
            headers={header: "secret"},
            auth=None,
            timeout=httpx.Timeout(240),
            follow_redirects=True,
        )


def test_default_factory_supplies_no_headers_or_auth(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class Transport:
        def __init__(self, **kwargs: Any) -> None:
            captured["transport"] = kwargs

    class FastClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client"] = kwargs

    monkeypatch.setattr(client_module, "StreamableHttpTransport", Transport)
    monkeypatch.setattr(client_module, "Client", FastClient)

    client_module._default_client_factory("http://127.0.0.1:8000/mcp", 240.0)

    assert captured["transport"] == {
        "url": "http://127.0.0.1:8000/mcp",
        "httpx_client_factory": client_module._DirectHttpxClientFactory(
            "http://127.0.0.1:8000/mcp"
        ),
    }
    assert captured["client"]["timeout"] == 240.0


def test_direct_http_client_does_not_follow_redirect(monkeypatch) -> None:
    target_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal target_requests
        if request.url.host == "attacker.invalid":
            target_requests += 1
            return httpx.Response(200)
        return httpx.Response(
            302, headers={"location": "http://attacker.invalid/steal"}
        )

    original = client_module.httpx.AsyncClient

    def with_mock_transport(**kwargs: Any) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", with_mock_transport)
    client = client_module._DirectHttpxClientFactory("http://127.0.0.1:8000/mcp")(
        headers={}, auth=None, timeout=httpx.Timeout(240), follow_redirects=True
    )

    async def request() -> httpx.Response:
        async with client:
            return await client.get("http://127.0.0.1:8000/mcp")

    response = asyncio.run(request())
    assert response.status_code == 302
    assert target_requests == 0


def test_hostile_proxy_environment_cannot_intercept_loopback(monkeypatch) -> None:
    proxy_requests = 0
    target_requests = 0
    received_headers: dict[str, str] = {}

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal proxy_requests
            proxy_requests += 1
            self.send_response(502)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal target_requests
            target_requests += 1
            received_headers.update(self.headers.items())
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    proxy_thread.start()
    target_thread.start()
    proxy_url = f"http://127.0.0.1:{proxy.server_port}"
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("ALL_PROXY", proxy_url)
    monkeypatch.setenv("NO_PROXY", "")

    client = client_module._DirectHttpxClientFactory(
        f"http://127.0.0.1:{target.server_port}/mcp"
    )(
        headers={"Accept": "application/json"},
        auth=None,
        timeout=httpx.Timeout(5),
        follow_redirects=True,
    )

    async def request() -> httpx.Response:
        async with client:
            return await client.get(f"http://127.0.0.1:{target.server_port}/mcp")

    try:
        response = asyncio.run(request())
    finally:
        proxy.shutdown()
        target.shutdown()
        proxy.server_close()
        target.server_close()
        proxy_thread.join(timeout=2)
        target_thread.join(timeout=2)

    assert response.status_code == 200
    assert proxy_requests == 0
    assert target_requests == 1
    assert received_headers["Host"] == f"127.0.0.1:{target.server_port}"
    lowered = {name.casefold() for name in received_headers}
    forbidden_on_wire = client_module._FORBIDDEN_FORWARDING_HEADERS - {"host"}
    assert not lowered.intersection(forbidden_on_wire)
    assert not any(name.startswith("x-forwarded-") for name in lowered)


@pytest.mark.asyncio
async def test_endpoint_is_immutable_and_corruption_fails_before_factory() -> None:
    factory = RecordingFactory()
    client = MCPClient("http://127.0.0.1:8000/mcp", client_factory=factory)

    with pytest.raises(AttributeError, match="immutable"):
        client._endpoint = "http://192.0.2.1:8000/mcp"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        client.url = "http://192.0.2.1:8000/mcp"  # ty: ignore[invalid-assignment]

    object.__setattr__(client, "_endpoint", "http://192.0.2.1:8000/mcp")
    with pytest.raises(MCPClientError) as caught:
        await client.list_tools()

    assert caught.value.details.error_class is ErrorClass.UNKNOWN
    assert factory.arguments == []


@pytest.mark.asyncio
async def test_request_boundary_rejects_a_different_endpoint_before_transport() -> None:
    boundary = client_module._RequestBoundary("http://127.0.0.1:8000/mcp")
    request = httpx.Request("POST", "http://127.0.0.2:8000/mcp")

    with pytest.raises(httpx.RequestError, match="left its configured"):
        await boundary(request)
