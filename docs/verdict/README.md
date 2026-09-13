# Verdict block — staged design and wiring

These files are staged, not wired. The engine, server, frontend and tests are not
yet in this repository, so nothing here is reachable from a running app. Each file
is recorded at the path it was authored against, with its destination below.

| File | Destination | Notes |
|---|---|---|
| `server.py` | `backend/server.py` | Full file, already carrying the verdict call in `persist_result`. Imports `.config`, `.data`, `.engine`, `.experiments`, `.verdict` — none of which are in this repository yet. |
| `wiring.patch` | applied, not stored | Diff against the pre-verdict `backend/server.py` and `frontend/src/components/Results.jsx`. The server half is already folded into `server.py` above; the `Results.jsx` half is still unapplied. |
| `verdict-styles.css` | append to `frontend/src/styles.css` | Self-host Fraunces, Inter and JetBrains Mono rather than using the Google CDN. |
| `verdict-preview.html` | reference only | Static preview of all six verdict tones plus the first-run screen. Uses the Google Fonts CDN because it is a standalone preview. |

## Still missing

`backend/verdict.py`, `frontend/src/components/Verdict.jsx` and
`frontend/src/components/FirstRun.jsx` are referenced by the patch and the styles
but were never handed over. The verdict block cannot render without all three.

## Design intent

The verdict is the first thing on the results page, above the metrics: one sentence
answering "did this work, and can I trust it", then where the money went, then
everything else. Rail colour is position on the prismatic spectrum, used as the
confidence scale itself — red through amber to mint — not as decoration.

Tones, in order of that scale: `blown` (costs ate a working signal), `loss`,
`thin` (profitable but barely clears costs), `unproven` (won, but in-sample),
`holds` (held up out of sample), `empty` (no trades taken).
