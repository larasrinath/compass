# Connector pagination for Compass

Thank you to **Daniel Sticker** and the contributors to
[linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server).
Compass uses their connector for LinkedIn access. This small patch adds explicit
people-search pages while keeping one navigation per tool call.

The connector and this derivative patch are **Apache-2.0**, separately from
Compass's MIT license. The upstream LICENSE and NOTICE are included here.

## Install

Use a separate connector checkout; do not apply this patch in the Compass root.
The tested upstream base is `f410bfdc32569f8763fde11338b24ec6a0797f0d`.
From the Compass repository root:

```sh
git clone https://github.com/stickerdaniel/linkedin-mcp-server.git ../compass-linkedin-mcp
git -C ../compass-linkedin-mcp switch -c compass-pagination f410bfdc32569f8763fde11338b24ec6a0797f0d
git -C ../compass-linkedin-mcp am "$PWD/integrations/linkedin-mcp-server/people-pagination.patch"
cd ../compass-linkedin-mcp
uv sync
uv run python -m linkedin_mcp_server --transport streamable-http --host 127.0.0.1 --port 8000 --no-auto-import
```

Retain the connector's existing login and follow its normal setup if this is a
new installation. Do not replace it with an unpatched `uvx` release: that would
remove the `page` tool argument. Stop an existing connector before starting the
patched one on the same port.

## Contract

`search_people` adds an optional integer `page` in the range 1–1000, default 1.
Each call returns that requested page number and preserves the existing filters
and per-page reference cap. It neither downloads 1,000 profiles in one request
nor promises that LinkedIn exposes 1,000 results. Compass queues consecutive
pages, deduplicates identities, and queues each new profile separately.

The patch is derived from local connector commit `e5b8690`. All implementation,
test and documentation changes are contained in the patch, so installing Compass
does not depend on an uncommitted local connector checkout. Verification:

```sh
uv run python -m pytest tests/test_scraping.py tests/test_tools.py -k 'search_people or TestSearchPeople'
```

See [Compass verification notes](../../docs/reviews/search-pagination.md).
