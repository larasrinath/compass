# Native Windows startup verification

Windows PowerShell uses `.\compass`; macOS/Linux use `./compass`. The Windows
command resolves to `compass.cmd` and installs uv, Python, the pinned connector,
and Node/frontend dependencies automatically. Git remains a prerequisite, as on
macOS. All options are forwarded to the shared launcher.

Implemented paths include Windows Node ZIP downloads with checksum checks,
direct npm CLI execution, interprocess locks, NTFS private storage, cooperative
dashboard restart, and owned connector/browser cleanup through Windows Job Objects.
The connector runs with `--no-daemon` so it remains owned by this launcher.

## Local evidence (macOS, Python 3.13)

- Launcher, platform support, startup and queue: **70 passed, 4 skipped**.
  The skipped tests require native Windows: two command spellings, ACL repair,
  and cleanup after an intermediate process exits.
- Database regression suite: **152 passed**. Its expected migration list was
  brought up to date with the already-existing `0033_app_configuration` migration.
- `./compass --setup-only`: **passed**, including actual connector installation
  and frontend build.
- PowerShell 7.4.6 parser: bootstrap script **passed** syntax validation.
- Ruff lint/format and type checks for backend plus changed launcher/platform
  tests: **passed**.

## Limits and existing failures

The [platform workflow](https://github.com/larasrinath/compass/actions/workflows/platform-startup.yml)
executes real Windows command resolution, locking, permissions, child-process
tests, and fresh/cached setup on native GitHub runners, alongside macOS and Linux.
Consult the workflow run for the commit being evaluated for its CI results.
Interactive LinkedIn sign-in was not exercised.

The broad backend test attempt was interrupted to diagnose repeated scoring
fixture errors. The same scoring setup error, source-contract failure, OpenAPI
route-list failure, and automatic-download timeout were reproduced on untouched
base `440391aabff974d456a1f4c36918ec84b0b195e0`. No full-suite pass is claimed.
The repository-wide type check retains 13 existing diagnostics in
`tests/unit/test_queue.py`; the old nullable-stdout diagnostic in the touched
launcher test has been fixed.
