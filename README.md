# NBA Futures Historical Archive

- Season: 2026-27
- Started: 2026-08-29
- Source: RotoWire
- Daily RAW JSON snapshots
- Snapshot dates use `America/New_York`
- GitHub Actions archives data automatically each day

This repository is currently used only for historical data archival research.

## Partial source outages

Each market is validated independently. When at least one market is fetched
successfully, unavailable markets use the latest validated snapshot from the same
season (including an earlier run that day). Markets with no valid history remain
missing. Raw response bytes are preserved.

`_meta.json` records `success` for a fresh fetch, `stale` for carried-forward data,
and `source_date` for the original observation date. Carrying data forward again
does not advance its source date. Consumers must check this metadata before
treating odds as current. Fetch errors remain recorded even when fallback succeeds.

If all endpoints return empty arrays, the run succeeds without changing any
snapshots. If nothing valid is fetched and there are network or validation errors,
the run fails without changing snapshots. Partial availability no longer fails
the entire run just because MVP is missing. Freshness metadata is committed even
when the raw odds are unchanged.

A successful HTTP response does not guarantee available odds: RotoWire can return
HTTP 200 with `[]`. Re-running cannot recover past odds that were never archived.
