from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_DROP_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "auth_token",
    "authorization",
    "browser_profile_path",
    "cookie_path",
    "credential",
    "credentials",
    "cwd",
    "db_path",
    "dir",
    "directory",
    "hostname",
    "issue_template_path",
    "mcp_url",
    "path",
    "portable_cookie_path",
    "proxy_password",
    "proxy_username",
    "refresh_token",
    "runtime",
    "runtime_storage_state_path",
    "source_profile_dir",
    "suggested_gist_command",
    "user_data_dir",
    "x_api_key",
    "working_directory",
}
_DROP_KEY_SUFFIXES = ("_dir", "_directory", "_path")
_DROP_KEY_COMPACT = {re.sub(r"[^a-z0-9]", "", key.casefold()) for key in _DROP_KEYS}

_MAX_PERCENT_DECODE_PASSES = 12
_MAX_PERCENT_DECODE_CHARS = 65_536
_PERCENT_ESCAPE = re.compile(r"%[0-9a-f]{2}", re.IGNORECASE)
_MALFORMED_PERCENT_ESCAPE = re.compile(r"%(?![0-9a-f]{2})", re.IGNORECASE)
_MALFORMED_PERCENT_TOKEN = re.compile(r"%[0-9a-z]{0,2}", re.IGNORECASE)

_MALFORMED_JSON_BODY = b'{"detail":"Response could not be safely serialized"}'
_URL_AUTHORITY = re.compile(
    r"(?P<prefix>[a-z][a-z0-9+.-]*://|(?<![a-z0-9:/?#])//)"
    r"(?P<authority>[^\s/?#]*)",
    flags=re.IGNORECASE,
)
_UTF8_BOM = b"\xef\xbb\xbf"
_WINDOWS_PATH = re.compile(
    r"(?<![a-z0-9])(?:[a-z]:[\\/]|\\\\)[^\s\r\n\"'<>]+",
    flags=re.IGNORECASE,
)
_FILE_URL = re.compile(
    r"\bfile:(?://[^\s\"'<>]*)?(?:[^\s\"'<>]*)",
    flags=re.IGNORECASE,
)
_NETWORK_URL = re.compile(r"(?:(?:https?):)?//[^\s\"'<>]+", re.IGNORECASE)
_UNIX_PATH = re.compile(r"(?<![a-z0-9/])/(?!/)[^\s\"'<>]+", re.IGNORECASE)
_COMMON_FILESYSTEM_PATH = re.compile(
    r"(?:^|/)(?:etc|home|opt|private|srv|tmp|users|var)(?:/|$)",
    re.IGNORECASE,
)
_COMPONENT_PARAMETER = re.compile(
    r"(?P<separator>^|[?&;])(?P<key>[^=?&#;]*)"
    r"(?P<equals>=)(?P<value>[^?&#;]*)"
)
_MATRIX_PARAMETER = re.compile(
    r"(?P<separator>;)(?P<key>[^=;/]*)"
    r"(?P<equals>=)(?P<value>[^;/]*)"
)
_SECRET_LABEL = (
    r"(?:access[ _-]?(?:credentials?|key|token)|api[ _-]?key|"
    r"auth(?:orization|[ _-]?token)?|client[ _-]?(?:key|secret)|"
    r"cookie(?:[ _-]?path)?|credentials?|key|li_at|pass[ _-]?word|"
    r"proxy[ _-]?(?:password|username)|refresh[ _-]?token|secret|"
    r"session[ _-]?token|token|x[ _-]?api[ _-]?key)"
)
_SECRET_BOUNDARY = rf"(?=\s+\b{_SECRET_LABEL}\s*[:=]|[;,&?#\r\n]|$)"
_LABELED_SECRET = re.compile(
    rf"(?P<label>\b{_SECRET_LABEL}\s*[:=]\s*)"
    rf"(?:bearer\s+)?(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|.+?{_SECRET_BOUNDARY})",
    re.IGNORECASE,
)
_AUTHORIZATION_BEARER = re.compile(
    r"(?P<label>\bauthorization(?:\s*[:=]\s*|\s+)bearer\s+)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|.+?(?=[;,&?#\r\n]|$))",
    re.IGNORECASE,
)
_BEARER_SECRET = re.compile(
    r"(?P<label>\bbearer\s+)"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|.+?(?=[;,&?#\r\n]|$))",
    re.IGNORECASE,
)
_QUOTED_LABELED_VALUE = re.compile(
    r"(?P<label>(?P<label_quote>[\"'])(?P<label_name>[^\"']+)"
    r"(?P=label_quote)\s*[:=]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|.+?(?=[,;}\r\n]|$))",
    re.IGNORECASE,
)
_EMBEDDED_LABELED_VALUE = re.compile(
    r"(?P<label>"
    r"(?:(?P<quote>[\"'])(?P<quoted_name>[^\"'\r\n]{1,96})(?P=quote)"
    r"|(?<![a-z0-9_.-])(?P<bare_name>client[ ]+key|[a-z][a-z0-9_.-]{0,95}))"
    r"\s*[:=]\s*)",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_KEYS = {
    "accesskey",
    "accesstoken",
    "apikey",
    "auth",
    "authorization",
    "authtoken",
    "cookie",
    "cookiepath",
    "credential",
    "credentials",
    "key",
    "liat",
    "password",
    "proxypassword",
    "proxyusername",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "token",
    "xapikey",
}
_LINKEDIN_URL_KEYS = {
    "profile_url",
    "relative_url",
    "result_url",
    "tool_url",
    "url",
}
_LINKEDIN_RELATIVE_PATH = re.compile(
    r"^/(?:"
    r"(?:in|company)/[^/?#]+/?|"
    r"jobs/view/[0-9]+/?|"
    r"feed/update/[^/?#]+/?|"
    r"posts/[^/?#]+/?|"
    r"messaging/(?:compose/?)?|"
    r"search/(?:results/[^?#]*)?"
    r")$",
    re.IGNORECASE,
)
_OPENAPI_ROUTE_KEY = re.compile(
    r"^/api(?:/[a-z0-9._~!$&'()*+,;=:@{}-]+)*$",
    re.IGNORECASE,
)
_OPENAPI_SCHEMA_REFERENCE = re.compile(
    r"^#/components/schemas/[a-z0-9._-]+$",
    re.IGNORECASE,
)
_DROP_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authenticate",
    "proxy-authorization",
    "server",
    "set-cookie",
    "www-authenticate",
    "x-powered-by",
}
_SENSITIVE_HEADER_PARTS = {
    "auth",
    "authorization",
    "cookie",
    "credential",
    "key",
    "password",
    "path",
    "runtime",
    "secret",
    "token",
}
_SENSITIVE_HEADER_COMPACT = _SENSITIVE_QUERY_KEYS | {
    "credentials",
    "proxyauthorization",
    "setcookie",
}
_STRUCTURAL_HEADER_NAMES = ("content-length",)


def _is_json_media_type(content_type: str) -> bool:
    media_type = content_type.partition(";")[0].strip().casefold()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


def _linkedin_relative_url_shadow(value: str, field_name: str | None) -> str | None:
    if field_name not in _LINKEDIN_URL_KEYS or "\\" in value:
        return None
    decoded, _, complete = _bounded_unquote(value)
    if not complete or "\\" in decoded:
        return None
    parsed = urlsplit(decoded)
    path_without_matrix = "/".join(
        segment.partition(";")[0] for segment in parsed.path.split("/")
    )
    if (
        not parsed.scheme
        and not parsed.netloc
        and bool(_LINKEDIN_RELATIVE_PATH.fullmatch(path_without_matrix))
    ):
        return decoded
    return None


def _compact_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fully_unquote(value).casefold())


def _fully_unquote(value: str) -> str:
    decoded, _, _ = _bounded_unquote(value)
    return decoded


def _bounded_unquote(value: str) -> tuple[str, bool, bool]:
    """Build a bounded decoding shadow without mutating the returned payload."""
    if "%" not in value and "+" not in value:
        return value, False, True
    if len(value) > _MAX_PERCENT_DECODE_CHARS:
        return value, False, False

    decoded = value
    for _ in range(_MAX_PERCENT_DECODE_PASSES):
        if _MALFORMED_PERCENT_ESCAPE.search(decoded):
            return decoded, decoded != value, False
        next_value = unquote_plus(decoded)
        if next_value == decoded:
            return decoded, decoded != value, True
        if len(next_value) > _MAX_PERCENT_DECODE_CHARS:
            return decoded, decoded != value, False
        decoded = next_value
    return (
        decoded,
        decoded != value,
        not (
            _PERCENT_ESCAPE.search(decoded) or _MALFORMED_PERCENT_ESCAPE.search(decoded)
        ),
    )


def _normalized_identifier_is_sensitive(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    sensitive_markers = (
        "accesscredential",
        "accesskey",
        "accesstoken",
        "apikey",
        "authtoken",
        "clientkey",
        "clientsecret",
        "cookiepath",
        "credential",
        "password",
        "privatekey",
        "proxypassword",
        "proxyusername",
        "refreshtoken",
        "secretkey",
        "sessiontoken",
        "xapikey",
    )
    return (
        compact in _SENSITIVE_QUERY_KEYS
        or any(marker in compact for marker in sensitive_markers)
        or compact.endswith(
            (
                "token",
                "tokens",
                "password",
                "passwords",
                "secret",
                "secrets",
                "cookie",
                "cookies",
                "credential",
                "credentials",
            )
        )
        or compact.startswith("auth")
    )


def _malformed_decoding_shadows(value: str) -> tuple[str, str, str]:
    """Expose literal-percent and invalid-token evasions for inspection."""
    return (
        value.replace("%", ""),
        re.sub(r"%[0-9a-z]?", "", value, flags=re.IGNORECASE),
        _MALFORMED_PERCENT_TOKEN.sub("", value),
    )


def _is_sensitive_identifier(value: str) -> bool:
    decoded, _, complete = _bounded_unquote(value)
    if complete:
        return _normalized_identifier_is_sensitive(decoded)
    if not _MALFORMED_PERCENT_ESCAPE.search(decoded):
        return True
    return any(
        _normalized_identifier_is_sensitive(shadow)
        for shadow in _malformed_decoding_shadows(decoded)
    )


def _embedded_label_is_sensitive(value: str) -> bool:
    """Recognize normalized secret and internal-diagnostic labels."""
    return _drop_key(value) or _is_sensitive_identifier(value)


def _quoted_value_end(value: str, start: int) -> int:
    quote = value[start]
    escaped = False
    for index in range(start + 1, len(value)):
        character = value[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == quote:
            return index + 1
    return len(value)


def _collection_value_end(value: str, start: int) -> int:
    closing = {"{": "}", "[": "]", "(": ")"}
    stack = [closing[value[start]]]
    quote: str | None = None
    escaped = False
    for index in range(start + 1, len(value)):
        character = value[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in closing:
            stack.append(closing[character])
        elif character == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
    return len(value)


def _embedded_value_end(value: str, start: int) -> int:
    if start >= len(value):
        return start
    if value[start] in {'"', "'"}:
        return _quoted_value_end(value, start)
    if value[start] in "{[(":
        return _collection_value_end(value, start)
    constructor = re.match(r"[a-z_][a-z0-9_.]*\s*(?P<open>\()", value[start:], re.I)
    if constructor is not None:
        opening = start + constructor.start("open")
        return _collection_value_end(value, opening)
    boundary = re.search(r"[,;&#?\r\n]", value[start:])
    return len(value) if boundary is None else start + boundary.start()


def _redact_embedded_labeled_values(value: str) -> str:
    """Redact complete scalar/collection values while retaining surrounding prose."""
    output: list[str] = []
    emit_cursor = 0
    search_cursor = 0
    while match := _EMBEDDED_LABELED_VALUE.search(value, search_cursor):
        label_name = match.group("quoted_name") or match.group("bare_name")
        if not _embedded_label_is_sensitive(label_name):
            search_cursor = match.end()
            continue
        value_start = match.end()
        value_end = _embedded_value_end(value, value_start)
        output.extend((value[emit_cursor : match.start()], "[redacted]"))
        emit_cursor = value_end
        search_cursor = value_end
    if not output:
        return value
    output.append(value[emit_cursor:])
    return "".join(output)


def _quoted_label_is_sensitive(match: re.Match[str]) -> bool:
    return _is_sensitive_identifier(match.group("label_name"))


def _redact_quoted_labeled_value(match: re.Match[str]) -> str:
    if not _quoted_label_is_sensitive(match):
        return match.group(0)
    value = match.group("value")
    quote = value[0] if value[:1] in {'"', "'"} else ""
    return f"{match.group('label')}{quote}[redacted]{quote}"


def _contains_sensitive_quoted_label(value: str) -> bool:
    return any(
        _quoted_label_is_sensitive(match)
        for match in _QUOTED_LABELED_VALUE.finditer(value)
    )


def _parameter_value_is_sensitive(value: str) -> bool:
    decoded, _, complete = _bounded_unquote(value)
    if not complete:
        return True
    normalized = decoded.casefold()
    return (
        bool(_LABELED_SECRET.search(decoded))
        or bool(_AUTHORIZATION_BEARER.search(decoded))
        or bool(_FILE_URL.search(decoded))
        or bool(_WINDOWS_PATH.search(decoded))
        or bool(_UNIX_PATH.search(decoded))
        or bool(_NETWORK_URL.search(decoded))
        or normalized.startswith("//")
        or ".linkedin-mcp" in normalized
        or str(Path.home()).casefold() in normalized
    )


def _sanitize_parameter_component(value: str) -> str:
    def redact_parameter(match: re.Match[str]) -> str:
        key = match.group("key")
        _, _, key_complete = _bounded_unquote(key)
        if not (
            not key_complete
            or _is_sensitive_identifier(key)
            or _parameter_value_is_sensitive(match.group("value"))
        ):
            return match.group(0)
        safe_key = key if key_complete else "[redacted]"
        return f"{match.group('separator')}{safe_key}{match.group('equals')}[redacted]"

    return _COMPONENT_PARAMETER.sub(redact_parameter, value)


def _sanitize_url_components(value: str) -> str:
    """Redact credentials from query, fragment, and path matrix parameters."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[redacted-url]"

    def redact_matrix(match: re.Match[str]) -> str:
        key = match.group("key")
        _, _, key_complete = _bounded_unquote(key)
        if not (
            not key_complete
            or _is_sensitive_identifier(key)
            or _parameter_value_is_sensitive(match.group("value"))
        ):
            return match.group(0)
        safe_key = key if key_complete else "[redacted]"
        return f"{match.group('separator')}{safe_key}{match.group('equals')}[redacted]"

    path = _MATRIX_PARAMETER.sub(redact_matrix, parsed.path)
    query = _sanitize_parameter_component(parsed.query)
    fragment = _sanitize_parameter_component(parsed.fragment)
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))


def _is_permitted_linkedin_network_url(value: str, field_name: str | None) -> bool:
    if field_name not in _LINKEDIN_URL_KEYS:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def _has_openapi_private_marker(value: str) -> bool:
    decoded, _, complete = _bounded_unquote(value)
    if not complete:
        return True
    normalized = decoded.casefold()
    segments = decoded.replace("#", "").split("/")
    return (
        any(segment in {".", ".."} for segment in segments)
        or bool(_FILE_URL.search(decoded))
        or bool(_WINDOWS_PATH.search(decoded))
        or ".linkedin-mcp" in normalized
        or str(Path.home()).casefold() in normalized
    )


def _is_openapi_route_key(
    value: str,
    *,
    trusted_openapi: bool,
    openapi_paths_container: bool,
) -> bool:
    return (
        trusted_openapi
        and openapi_paths_container
        and bool(_OPENAPI_ROUTE_KEY.fullmatch(value))
        and not _has_openapi_private_marker(value)
    )


def _is_openapi_schema_reference(
    value: str,
    field_name: str | None,
    *,
    trusted_openapi: bool,
) -> bool:
    return (
        trusted_openapi
        and field_name == "$ref"
        and bool(_OPENAPI_SCHEMA_REFERENCE.fullmatch(value))
        and not _has_openapi_private_marker(value)
    )


def _decoded_shadow_is_sensitive(value: str, decoded: str) -> str | None:
    """Classify private material visible only after percent decoding."""
    raw_has_network_url = bool(_NETWORK_URL.search(value))
    if raw_has_network_url:
        # URL components are sanitized individually so benign encoded syntax survives.
        if _sanitize_url_components(value) != value:
            return None
        try:
            raw_path = urlsplit(value).path
            decoded_path = urlsplit(decoded).path
        except ValueError:
            return "[redacted-url]"
        if decoded_path == raw_path:
            return None
        normalized_path = decoded_path.casefold()
        if (
            _LABELED_SECRET.search(decoded_path)
            or _redact_embedded_labeled_values(decoded_path) != decoded_path
        ):
            return "[redacted]"
        if (
            _FILE_URL.search(decoded_path)
            or _WINDOWS_PATH.search(decoded_path)
            or _COMMON_FILESYSTEM_PATH.search(decoded_path)
            or ".linkedin-mcp" in normalized_path
            or str(Path.home()).casefold() in normalized_path
        ):
            return "[redacted-path]"
        if _NETWORK_URL.search(decoded_path):
            return "[redacted-url]"
        return None

    normalized = decoded.casefold()
    if (
        _LABELED_SECRET.search(decoded)
        or _AUTHORIZATION_BEARER.search(decoded)
        or _BEARER_SECRET.search(decoded)
        or _contains_sensitive_quoted_label(decoded)
        or _redact_embedded_labeled_values(decoded) != decoded
    ):
        return "[redacted]"
    if (
        _FILE_URL.search(decoded)
        or _WINDOWS_PATH.search(decoded)
        or ".linkedin-mcp" in normalized
        or str(Path.home()).casefold() in normalized
    ):
        return "[redacted-path]"

    if _NETWORK_URL.search(decoded):
        return "[redacted-url]"
    if _UNIX_PATH.search(decoded):
        return "[redacted-path]"
    return None


def _redact_authority(match: re.Match[str]) -> str:
    authority = match.group("authority")
    decoded_authority, _, complete = _bounded_unquote(authority)
    if not complete:
        return f"{match.group('prefix')}[redacted]"
    if "@" not in decoded_authority:
        return match.group(0)
    host = decoded_authority.rsplit("@", 1)[1]
    return f"{match.group('prefix')}[redacted]@{host}"


def _redact_string(
    value: str,
    *,
    field_name: str | None = None,
    trusted_openapi: bool = False,
    openapi_paths_container: bool = False,
) -> str:
    if _is_openapi_route_key(
        value,
        trusted_openapi=trusted_openapi,
        openapi_paths_container=openapi_paths_container,
    ):
        return value
    if _is_openapi_schema_reference(
        value,
        field_name,
        trusted_openapi=trusted_openapi,
    ):
        return value
    linkedin_shadow = _linkedin_relative_url_shadow(value, field_name)
    if linkedin_shadow is not None:
        sanitized_shadow = _sanitize_url_components(linkedin_shadow)
        return (
            sanitized_shadow
            if sanitized_shadow != linkedin_shadow
            else _sanitize_url_components(value)
        )

    # Remove unsafe URL components before judging residual encoding. This lets a
    # malformed query label fail closed without sacrificing the surrounding URL.
    working = _sanitize_url_components(value) if _NETWORK_URL.search(value) else value

    decoded, changed, complete = _bounded_unquote(working)
    if not complete:
        if _NETWORK_URL.search(value):
            return "[redacted-url]"
        if not _MALFORMED_PERCENT_ESCAPE.search(decoded):
            return "[redacted-encoded]"
        for malformed_shadow in _malformed_decoding_shadows(decoded):
            if _decoded_shadow_is_sensitive(
                working, malformed_shadow
            ) or _is_sensitive_identifier(malformed_shadow):
                return "[redacted-encoded]"
        decoded = working
        changed = False
    if changed and _NETWORK_URL.search(working):
        sanitized_decoded_url = _URL_AUTHORITY.sub(
            _redact_authority, _sanitize_url_components(decoded)
        )
        if sanitized_decoded_url != decoded:
            return sanitized_decoded_url
    if changed and (replacement := _decoded_shadow_is_sensitive(working, decoded)):
        return replacement

    sanitized = _FILE_URL.sub("[redacted-path]", working)
    sanitized = sanitized.replace(str(Path.home()), "[redacted-home]")
    sanitized = sanitized.replace(".linkedin-mcp", "[redacted-profile]")

    preserved_urls: list[str] = []

    def preserve_network_url(match: re.Match[str]) -> str:
        url = _sanitize_url_components(match.group(0))
        if url.startswith("//") and not _is_permitted_linkedin_network_url(
            url, field_name
        ):
            return "[redacted-path]"
        url = _URL_AUTHORITY.sub(_redact_authority, url)
        preserved_urls.append(url)
        return f"[preserved-url-{len(preserved_urls) - 1}]"

    sanitized = _NETWORK_URL.sub(preserve_network_url, sanitized)
    sanitized = _URL_AUTHORITY.sub(_redact_authority, sanitized)
    sanitized = _redact_embedded_labeled_values(sanitized)
    sanitized = _QUOTED_LABELED_VALUE.sub(_redact_quoted_labeled_value, sanitized)
    sanitized = _LABELED_SECRET.sub(r"\g<label>[redacted]", sanitized)
    sanitized = _AUTHORIZATION_BEARER.sub(r"\g<label>[redacted]", sanitized)
    sanitized = _BEARER_SECRET.sub(r"\g<label>[redacted]", sanitized)
    sanitized = _WINDOWS_PATH.sub("[redacted-path]", sanitized)
    sanitized = _UNIX_PATH.sub("[redacted-path]", sanitized)
    for index, url in enumerate(preserved_urls):
        sanitized = sanitized.replace(f"[preserved-url-{index}]", url)
    return sanitized


def _redact_key(
    value: Any,
    *,
    trusted_openapi: bool,
    openapi_paths_container: bool,
) -> Any:
    return (
        _redact_string(
            value,
            trusted_openapi=trusted_openapi,
            openapi_paths_container=openapi_paths_container,
        )
        if isinstance(value, str)
        else value
    )


def _drop_key(value: Any) -> bool:
    raw = str(value)
    decoded, _, complete = _bounded_unquote(raw)
    if not complete:
        if not _MALFORMED_PERCENT_ESCAPE.search(decoded):
            return True
        if _is_sensitive_identifier(raw):
            return True
        decoded = _MALFORMED_PERCENT_TOKEN.sub("", decoded)
    normalized = decoded.casefold()
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    identifier_like = bool(re.fullmatch(r"[a-z0-9 ._-]+", normalized))
    return (
        normalized in _DROP_KEYS
        or normalized.endswith(_DROP_KEY_SUFFIXES)
        or compact in _DROP_KEY_COMPACT
        or (identifier_like and compact.endswith(("dir", "directory", "path")))
        or (identifier_like and _is_sensitive_identifier(raw))
    )


def sanitize_for_frontend(
    value: Any,
    *,
    field_name: str | None = None,
    _trusted_openapi: bool = False,
    _location: tuple[str, ...] = (),
) -> Any:
    """Recursively remove process-local diagnostics and path material."""
    if isinstance(value, dict):
        return {
            _redact_key(
                key,
                trusted_openapi=_trusted_openapi,
                openapi_paths_container=_location == ("paths",),
            ): sanitize_for_frontend(
                child,
                field_name=str(key).casefold(),
                _trusted_openapi=_trusted_openapi,
                _location=(*_location, str(key)),
            )
            for key, child in value.items()
            if not _drop_key(key)
        }
    if isinstance(value, list):
        return [
            sanitize_for_frontend(
                child,
                field_name=field_name,
                _trusted_openapi=_trusted_openapi,
                _location=_location,
            )
            for child in value
        ]
    if isinstance(value, tuple):
        return [
            sanitize_for_frontend(
                child,
                field_name=field_name,
                _trusted_openapi=_trusted_openapi,
                _location=_location,
            )
            for child in value
        ]
    if isinstance(value, str):
        return _redact_string(
            value,
            field_name=field_name,
            trusted_openapi=_trusted_openapi,
        )
    return value


def _sensitive_header_name(name: str) -> bool:
    normalized = name.casefold()
    if normalized in _DROP_HEADER_NAMES:
        return True
    underscored = normalized.replace("-", "_")
    if _drop_key(underscored):
        return True
    parts = set(re.split(r"[-_]", normalized))
    compact = _compact_identifier(normalized)
    without_extension_prefix = compact[1:] if compact.startswith("x") else compact
    return (
        bool(parts & _SENSITIVE_HEADER_PARTS)
        or (without_extension_prefix in _SENSITIVE_HEADER_COMPACT)
        or _is_sensitive_identifier(without_extension_prefix)
    )


def _is_safe_application_location(value: str) -> bool:
    """Allow only validated local API redirects at the response boundary."""
    if any(character in value for character in "\r\n\\"):
        return False
    decoded, _, complete = _bounded_unquote(value)
    if not complete:
        return False
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc:
        return False
    if parsed.path != "/api" and not parsed.path.startswith("/api/"):
        return False
    if any(segment in {".", ".."} for segment in parsed.path.split("/")):
        return False
    if (
        _FILE_URL.search(decoded)
        or _WINDOWS_PATH.search(decoded)
        or _COMMON_FILESYSTEM_PATH.search(parsed.path)
        or ".linkedin-mcp" in decoded.casefold()
        or str(Path.home()).casefold() in decoded.casefold()
    ):
        return False
    if any(
        _is_sensitive_identifier(segment)
        for segment in parsed.path.split("/")
        if segment
    ):
        return False
    return (
        _sanitize_url_components(decoded) == decoded
        and _redact_embedded_labeled_values(decoded) == decoded
    )


def _sanitize_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    sanitized: list[tuple[bytes, bytes]] = []
    for raw_name, raw_value in headers:
        name = raw_name.decode("latin-1").casefold()
        if _sensitive_header_name(name):
            continue
        if name == "location":
            location = raw_value.decode("latin-1")
            if not _is_safe_application_location(location):
                continue
            value = raw_value
        elif name in _STRUCTURAL_HEADER_NAMES:
            value = raw_value
        else:
            safe_value = _redact_string(raw_value.decode("latin-1"))
            try:
                value = safe_value.encode("latin-1")
            except UnicodeEncodeError:
                value = b"[redacted]"
        sanitized.append((raw_name, value))
    return sanitized


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not permitted: {value}")


def _strict_json_loads(value: bytes | str) -> Any:
    return json.loads(value, parse_constant=_reject_json_constant)


def _strict_json_dumps(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    serialized.encode("utf-8")
    return serialized


def _sanitize_sse_event(event: bytes, *, terminated: bool) -> bytes:
    try:
        text = event.decode("utf-8")
    except UnicodeDecodeError:
        safe = "event: error\ndata: Response could not be safely serialized"
        return (safe + ("\n\n" if terminated else "")).encode()

    output: list[str] = []
    data: list[str] = []
    data_position: int | None = None
    for line in text.split("\n"):
        field, separator, raw_value = line.partition(":")
        if field == "data" and (separator or line == "data"):
            if data_position is None:
                data_position = len(output)
            value = raw_value[1:] if raw_value.startswith(" ") else raw_value
            data.append(value)
        else:
            output.append(_redact_string(line))

    if data_position is not None:
        payload = "\n".join(data)
        try:
            value = _strict_json_loads(payload)
        except (json.JSONDecodeError, ValueError):
            candidate = payload.lstrip("\ufeff \t\r\n")
            if candidate.startswith(("{", "[")) or candidate in {
                "NaN",
                "Infinity",
                "-Infinity",
            }:
                safe_payload = _MALFORMED_JSON_BODY.decode()
            else:
                safe_payload = _redact_string(payload)
        else:
            try:
                safe_payload = _strict_json_dumps(sanitize_for_frontend(value))
            except (UnicodeEncodeError, ValueError, TypeError, OverflowError):
                safe_payload = _MALFORMED_JSON_BODY.decode()
        safe_data = [f"data: {line}" for line in safe_payload.split("\n")]
        output[data_position:data_position] = safe_data or ["data:"]

    suffix = "\n\n" if terminated else ""
    return ("\n".join(output) + suffix).encode("utf-8")


class _SSEParser:
    """Incrementally split an SSE stream on every legal line ending."""

    def __init__(self) -> None:
        self.buffer = b""
        self.event_lines: list[bytes] = []
        self.stream_started = False

    def feed(self, chunk: bytes, *, final: bool) -> bytes:
        self.buffer += chunk
        if not self.stream_started:
            if (
                not final
                and len(self.buffer) < len(_UTF8_BOM)
                and _UTF8_BOM.startswith(self.buffer)
            ):
                return b""
            if self.buffer.startswith(_UTF8_BOM):
                self.buffer = self.buffer[len(_UTF8_BOM) :]
            self.stream_started = True

        output: list[bytes] = []
        while True:
            line = self._pop_line(final=final)
            if line is None:
                break
            content, terminated = line
            if terminated and not content:
                if self.event_lines:
                    output.append(
                        _sanitize_sse_event(
                            b"\n".join(self.event_lines), terminated=True
                        )
                    )
                    self.event_lines.clear()
                continue
            self.event_lines.append(content)
            if not terminated:
                break

        if final and self.event_lines:
            output.append(
                _sanitize_sse_event(b"\n".join(self.event_lines), terminated=False)
            )
            self.event_lines.clear()
        return b"".join(output)

    def _pop_line(self, *, final: bool) -> tuple[bytes, bool] | None:
        for index, byte in enumerate(self.buffer):
            if byte == 0x0A:
                content = self.buffer[:index]
                self.buffer = self.buffer[index + 1 :]
                return content, True
            if byte == 0x0D:
                if index + 1 == len(self.buffer) and not final:
                    return None
                width = 2 if self.buffer[index + 1 : index + 2] == b"\n" else 1
                content = self.buffer[:index]
                self.buffer = self.buffer[index + width :]
                return content, True
        if final and self.buffer:
            content = self.buffer
            self.buffer = b""
            return content, False
        return None


class PrivacyFilterMiddleware:
    """Sanitize every structured frontend response at the final boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start: Message | None = None
        chunks: list[bytes] = []
        response_kind = "passthrough"
        sse_parser = _SSEParser()
        trusted_openapi = scope.get("path") == "/api/openapi.json"

        async def capture(message: Message) -> None:
            nonlocal response_kind, start
            if message["type"] == "http.response.start":
                start = message
                headers = _sanitize_headers(list(message.get("headers", [])))
                message["headers"] = headers
                content_type = next(
                    (
                        value.decode("latin-1")
                        for key, value in headers
                        if key.decode("latin-1").casefold() == "content-type"
                    ),
                    "",
                )
                media_type = content_type.partition(";")[0].strip().casefold()
                if _is_json_media_type(content_type):
                    response_kind = (
                        "json-bodyless"
                        if scope.get("method") == "HEAD"
                        or message.get("status") in {204, 304}
                        else "json"
                    )
                elif media_type == "text/event-stream":
                    response_kind = "sse"
                    message["headers"] = [
                        (key, value)
                        for key, value in headers
                        if key.decode("latin-1").casefold() != "content-length"
                    ]
                    await send(message)
                else:
                    await send(message)
                return
            if message["type"] != "http.response.body" or start is None:
                await send(message)
                return
            if response_kind == "passthrough":
                await send(message)
                return
            if response_kind == "sse":
                await send(
                    {
                        **message,
                        "body": sse_parser.feed(
                            message.get("body", b""),
                            final=not message.get("more_body", False),
                        ),
                    }
                )
                return

            if response_kind == "json-bodyless":
                if message.get("more_body", False):
                    return
                await send(start)
                await send({"type": "http.response.body", "body": b""})
                return

            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            headers = list(start.get("headers", []))
            body = b"".join(chunks)
            try:
                payload = _strict_json_loads(body)
                body = _strict_json_dumps(
                    sanitize_for_frontend(
                        payload,
                        _trusted_openapi=trusted_openapi,
                    )
                ).encode("utf-8")
            except (
                UnicodeDecodeError,
                UnicodeEncodeError,
                json.JSONDecodeError,
                ValueError,
                TypeError,
                OverflowError,
            ):
                body = _MALFORMED_JSON_BODY
                start["status"] = 500
                headers = [
                    (key, value)
                    for key, value in headers
                    if key.decode("latin-1").casefold() != "content-type"
                ]
                headers.append((b"content-type", b"application/json"))

            headers = [
                (key, value)
                for key, value in headers
                if key.decode("latin-1").casefold() != "content-length"
            ]
            headers.append((b"content-length", str(len(body)).encode("ascii")))
            start["headers"] = headers
            await send(start)
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, capture)
