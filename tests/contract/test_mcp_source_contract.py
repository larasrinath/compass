from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _server_checkout() -> Path:
    configured = os.environ.get("LINKEDIN_MCP_SOURCE")
    if configured:
        return Path(configured)
    candidates = [PROJECT_ROOT.parent / "linkedin"]
    candidates.extend(parent / "linkedin" for parent in PROJECT_ROOT.parents)
    return next(
        (
            candidate
            for candidate in candidates
            if (candidate / "linkedin_mcp_server").is_dir()
        ),
        candidates[0],
    )


def _nested_async_signature(path: Path, function_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            return [argument.arg for argument in node.args.args]
    raise AssertionError(f"{function_name} was not found in {path}")


def _dict_literal_keys(path: Path, variable_name: str) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        target = node.target
        if (
            isinstance(target, ast.Name)
            and target.id == variable_name
            and isinstance(node.value, ast.Dict)
        ):
            return tuple(
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
    raise AssertionError(f"{variable_name} was not found in {path}")


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
        "page",
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


def test_profile_section_order_matches_sibling_source() -> None:
    fields = _server_checkout() / "linkedin_mcp_server" / "scraping" / "fields.py"
    if not fields.is_file():
        pytest.skip("sibling linkedin-mcp-server checkout is absent")

    from linkedin_dashboard.services.enrichment import PERSON_SECTIONS

    assert PERSON_SECTIONS == _dict_literal_keys(fields, "PERSON_SECTIONS")


def test_person_identifier_normalizer_matches_pinned_sibling_contract() -> None:
    checkout = _server_checkout()
    identifiers = checkout / "linkedin_mcp_server" / "scraping" / "identifiers.py"
    if not identifiers.is_file():
        pytest.skip("sibling linkedin-mcp-server identifier source is absent")
    digest = hashlib.sha256(identifiers.read_bytes()).hexdigest()
    assert (
        digest == "634176d8c0c6df2088fd674a940c477319e904537a2b4e0f2601c0f26bd56494"
    ), (
        "sibling identifier source drifted; review and deliberately update the "
        "dashboard parity corpus"
    )

    values = [
        "williamhgates",
        "WilliamHGates",
        "https://www.linkedin.com/in/williamhgates",
        "http://www.linkedin.com/in/williamhgates",
        "www.linkedin.com/in/williamhgates",
        "https://de.linkedin.com/in/williamhgates",
        "https://m.linkedin.com/in/williamhgates",
        "https://www.linkedin.com/mwlite/in/williamhgates",
        "https://www.linkedin.com/mwlite/profile/in/williamhgates",
        "https://www.linkedin.com/in/williamhgates?trk=profile#experience",
        "https://www.linkedin.com/in/williamhgates/recent-activity/all/",
        "%D0%B0%D0%BD%D0%B4%D1%80%D0%B5%D0%B9",
        "https://ru.linkedin.com/in/%D0%B0%D0%BD%D0%B4%D1%80%D0%B5%D0%B9",
        "%ZZ",
        "felix%",
        "felix%2",
        "felix%2Ffoo",
        "felix%20foo",
        ".",
        "..",
        "%252e%252e",
        "me",
        "ME",
        "%6d%65",
        "https://www.linkedin.com/in/%FF",
        "https://www.linkedin.com/in/a%2Fb",
        "https://www.linkedin.com/company/microsoft",
        "https://www.linkedin.com/feed/",
        "https://lnkd.in/eXaMpLe1",
        "https://evil-linkedin.com/in/williamhgates",
        "https://linkedin.com.example.test/in/williamhgates",
        "bill gates",
        "in/williamhgates",
        "",
        "   ",
        "/in/Alice/",
    ]
    script = """
import json, pathlib, sys, types
scraping = types.ModuleType('linkedin_mcp_server.scraping')
scraping.__path__ = [str(pathlib.Path.cwd() / 'linkedin_mcp_server' / 'scraping')]
sys.modules['linkedin_mcp_server.scraping'] = scraping
core = types.ModuleType('linkedin_mcp_server.core')
exceptions = types.ModuleType('linkedin_mcp_server.core.exceptions')
class InvalidReferenceError(Exception): pass
exceptions.InvalidReferenceError = InvalidReferenceError
sys.modules['linkedin_mcp_server.core'] = core
sys.modules['linkedin_mcp_server.core.exceptions'] = exceptions
from linkedin_mcp_server.scraping.identifiers import normalize_person_identifier
values = json.loads(sys.stdin.read())
out = []
for value in values:
    try:
        out.append([True, normalize_person_identifier(value)])
    except Exception:
        out.append([False, None])
print(json.dumps(out, ensure_ascii=False))
"""
    environment = {**os.environ, "PYTHONPATH": str(checkout)}
    upstream = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(values),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        cwd=checkout,
    )
    assert upstream.returncode == 0, upstream.stderr
    expected = json.loads(upstream.stdout)

    from linkedin_dashboard.parsing.identity import (  # local side of contract
        normalize_person_reference,
    )

    actual: list[list[str | bool | None]] = []
    for value in values:
        try:
            actual.append([True, normalize_person_reference(value)])
        except Exception:
            actual.append([False, None])
    assert actual == expected
