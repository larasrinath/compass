# Usage notes

## Startup help

Run `./compass` from the repository folder. The app opens at
[127.0.0.1:8787](http://127.0.0.1:8787/brief).

- **Sign-in cancelled:** choose **Sign in to LinkedIn** in Compass to try again.
- **Already running:** run `./compass` again. It stops the verified instance from
  this repository, waits for shutdown, then launches the current version. Saved
  work is preserved; an active download may be interrupted. If installation is
  still preparing, let that launch finish first.
- **Login expired:** run `./compass --login` to restart and sign in again.
- **Port already in use:** another service may own the port. Stop that service
  yourself or run `./compass --port 8788 --connector-port 8001`. Compass only stops
  a verified launcher from this repository; unrelated services are left alone.
- **Browser did not open:** use the URL printed in the terminal.
- **Startup failed:** check `.compass/connector.log` for details.

Compass uses a dedicated LinkedIn session in `~/.compass-linkedin/`. Enter your
password only in LinkedIn's window. Your everyday browser cookies are not imported.

## Download controls

**Stop discovery** ends further search pages; queued profile downloads continue.
Use the activity queue to cancel pending tasks. For an older search, select it
under **Results from**, then use **Download remaining profiles**.

## Source checks

Opening a saved passage does not mark it verified or change its score.
**Record source checks** in Compare matches is an optional audit requiring ten
distinct current passages and a note. It is separate from candidate comparison.
Unrecorded selections clear on reload or when scoring inputs change.

## Current limitations

- Navigating away from the brief discards unsaved edits. Save with **Continue to
  search** before leaving.
- A first credential-only brief requires a positive credential weight. Its setup
  currently involves saving another criterion first and configuring scoring.
- LinkedIn location is a search preference, not a guaranteed geographic filter.
  Check the location in the downloaded profile.
- Comparison selection is temporary: it survives opening and closing a profile,
  but resets on browser reload.

