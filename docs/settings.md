# Settings

Settings stores operational preferences in the local database. Role criteria,
locations, titles, industries, company, and network filters stay in **Role brief**.
Settings appears immediately before **How it works** in the sidebar.

## Downloads and search batches

| Control | Default | Allowed values | Effect |
| --- | --- | --- | --- |
| Download profiles automatically | On | On / off | New searches queue profile overview and experience downloads when enabled. |
| Profiles at a time | 2 | 1–2 | Maximum concurrent profile jobs, capped by connector capacity. Other operations remain exclusive. |
| Pause between reads | 3 seconds* | 0–60 seconds | Paces subsequent reads after a completed read. |
| New downloads per batch | 1,000 | 1–1,000 | Allowance for one new download batch; existing downloads are reused. |
| Continue through search pages | On | On / off | New searches continue through available pages when enabled. |
| Maximum search pages | 1,000 | 1–1,000 | Page ceiling for each new paginated search. |
| When the connector is busy | 30 seconds | 1–300 seconds | Delay before an eligible automatic retry. |
| When a request times out | 0 seconds | 0–300 seconds | Additional delay before an eligible automatic retry. |

*Before Settings is first saved, `INTER_CALL_DELAY_SECONDS` supplies the pacing
value. Its default is 3 seconds. Saved preferences take precedence afterward.

Choose **Save settings** to apply changes or **Discard changes** to restore saved
values. Concurrency and pacing affect upcoming reads; in-flight reads finish.
Batch and page limits are captured at batch creation, so existing batches retain
their original limits. Changing automatic downloads does not cancel queued work.
Use the activity controls to cancel pending tasks or stop further discovery.

The batch allowance is not a database limit. Saved candidates accumulate across
searches. Search pagination can end before the limit when results are exhausted,
a page repeats, retrieval fails, or discovery is stopped. With automatic downloads
off, results can remain unscored until you request **Download remaining profiles**.

The current patched connector supports two isolated profile tabs; older connectors
use one. Increasing a batch size does not increase concurrency. Retrieval opens
LinkedIn pages in the signed-in browser; it does not use a bulk profile API.

Retry timing does not change retry eligibility, retry counts, or rate-limit
cooldowns. Saving Settings neither starts the connector nor resumes a paused queue.

## Scoring weights

The **Scoring weights** sheet has its own **Save scoring weights** action. It
recalculates saved evidence locally without another LinkedIn read. Changes to the
role brief also trigger local rescoring. Source-check selections for superseded
scores are cleared.

Inputs cover required skills, optional skills, experience, titles, industries,
location, and credentials. A criterion without a brief input is inactive; its
saved weight does not contribute until applicable. Credentials with no brief
input cannot be edited. Network context has no scoring weight.

Expand **Location matching** below the save action to edit metro/region
equivalences as JSON. Those changes are saved together with scoring weights.

## API contract

- `GET /api/settings` returns the current eight operational fields.
- `PUT /api/settings` saves them. Use a complete body from GET; types and ranges
  are validated, and unknown fields are rejected. No credentials or connector
  startup paths are included.
- `POST /api/searches` resolves omitted or null `automatic_downloads` and `paginate`
  from Settings. Explicit booleans override defaults for that request. Clients
  requiring discovery only must send `automatic_downloads: false`; clients requiring
  one page must send `paginate: false`. Omitting flags no longer means false.
- `/api/weights` retains its separate versioned contract. Stale saves are rejected
  so one editor cannot silently overwrite a newer configuration.

The `0033_app_configuration` migration adds one operational-settings row. It does
not schedule downloads, modify historical batches, or change existing scores.
