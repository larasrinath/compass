from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _server_checkout() -> Path:
    configured = os.environ.get("LINKEDIN_MCP_SOURCE")
    return Path(configured) if configured else PROJECT_ROOT.parent / "linkedin"


def _nested_async_signature(path: Path, function_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            return [argument.arg for argument in node.args.args]
    raise AssertionError(f"{function_name} was not found in {path}")


def test_read_only_tool_signatures_match_sibling_source_without_importing_it() -> None:
    checkout = _server_checkout()
    person = checkout / "linkedin_mcp_server" / "tools" / "person.py"
    company = checkout / "linkedin_mcp_server" / "tools" / "company.py"
    messaging = checkout / "linkedin_mcp_server" / "tools" / "messaging.py"
    if not person.is_file() or not company.is_file() or not messaging.is_file():
        pytest.skip("sibling linkedin-mcp-server checkout is absent")

    assert _nested_async_signature(person, "search_people") == [
        "keywords",
        "ctx",
        "location",
        "network",
        "current_company",
        "extractor",
    ]
    assert _nested_async_signature(person, "get_person_profile") == [
        "linkedin_username",
        "ctx",
        "sections",
        "max_scrolls",
        "extractor",
    ]
    assert _nested_async_signature(company, "get_company_profile") == [
        "company_name",
        "ctx",
        "sections",
        "extractor",
    ]
    assert _nested_async_signature(messaging, "send_message") == [
        "linkedin_username",
        "message",
        "confirm_send",
        "ctx",
        "profile_urn",
        "extractor",
    ]


def test_runtime_never_imports_or_supervises_the_sibling_server() -> None:
    backend = PROJECT_ROOT / "backend" / "linkedin_dashboard"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in backend.rglob("*.py")
    )

    assert "import linkedin_mcp_server" not in source
    assert "from linkedin_mcp_server" not in source
    assert "subprocess" not in source
    assert "create_subprocess" not in source
