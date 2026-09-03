from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_LINKEDIN_HOST = re.compile(r"^(?:[a-z0-9-]+\.)*linkedin\.com$")
_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_IDENTIFIER = re.compile(r"^[\w-]+$")
_STRAY_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_RAW_REFUSED = re.compile(r"[\x00-\x1f\x7f\\]")
_DOT_SEGMENTS = {".", ".."}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class InvalidPersonReference(ValueError):
    pass


def _decode_once(value: str) -> str | None:
    if "%" not in value:
        return value
    if _STRAY_PERCENT.search(value):
        return None
    try:
        decoded = unquote(value, errors="strict")
    except UnicodeDecodeError:
        return None
    return None if "%" in decoded else decoded


def _identifier(value: str) -> str | None:
    decoded = _decode_once(value)
    if decoded is None or not decoded or decoded in _DOT_SEGMENTS:
        return None
    if re.search(r"[\s/\\?#]|[\x00-\x1f\x7f]", decoded):
        return None
    try:
        decoded.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return decoded if _IDENTIFIER.fullmatch(decoded) else None


def normalize_person_reference(value: str) -> str:
    """Match the sibling server's one-decode public-identifier contract."""
    original = value.strip()
    if not original:
        raise InvalidPersonReference("missing /in/ public identifier")
    before_query = re.split(r"[?#]", original, maxsplit=1)[0]
    if _RAW_REFUSED.search(before_query):
        raise InvalidPersonReference("invalid /in/ public identifier")

    looks_like_path = original.startswith(("/", "//"))
    looks_like_url = (
        looks_like_path
        or bool(_HAS_SCHEME.match(original))
        or ("/" in original and "." in original.split("/", 1)[0])
    )
    if looks_like_url:
        if _HAS_SCHEME.match(original):
            candidate = original
        elif original.startswith("//"):
            candidate = f"https:{original}"
        elif original.startswith("/"):
            candidate = f"https://www.linkedin.com{original}"
        else:
            candidate = f"https://{original}"
        try:
            parsed = urlparse(candidate)
            port = parsed.port
        except ValueError as error:
            raise InvalidPersonReference("invalid /in/ public identifier") from error
        host = (parsed.hostname or "").casefold().removesuffix(".")
        if parsed.scheme not in _DEFAULT_PORTS or not _LINKEDIN_HOST.fullmatch(host):
            raise InvalidPersonReference("reference is not a LinkedIn profile")
        if port is not None and port != _DEFAULT_PORTS[parsed.scheme]:
            raise InvalidPersonReference("reference uses an unsupported port")
        raw_segments = parsed.path.split("/")
        if any(segment == "" for segment in raw_segments[1:-1]):
            raise InvalidPersonReference("invalid LinkedIn profile path")
        segments = [segment for segment in raw_segments if segment]
        if any(
            segment in _DOT_SEGMENTS or _decode_once(segment) in _DOT_SEGMENTS
            for segment in segments
        ):
            raise InvalidPersonReference("profile path contains a dot segment")
        if segments and segments[0].casefold() == "mwlite":
            segments.pop(0)
            if segments and segments[0].casefold() == "profile":
                segments.pop(0)
        if len(segments) < 2 or segments[0].casefold() != "in":
            raise InvalidPersonReference("reference is not a personal profile")
        username = _identifier(segments[1])
    else:
        username = _identifier(original)

    if username is None:
        raise InvalidPersonReference("invalid /in/ public identifier")
    if username.casefold() == "me":
        raise InvalidPersonReference("the /in/me alias cannot identify a candidate")
    return username


def canonical_profile_url(username: str) -> str:
    from urllib.parse import quote

    return f"https://www.linkedin.com/in/{quote(username, safe='')}"
