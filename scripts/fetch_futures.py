#!/usr/bin/env python3
"""Archive daily raw RotoWire NBA futures and awards JSON snapshots."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from http.client import IncompleteRead
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SEASON = "2026-27"
TIMEZONE_NAME = "America/New_York"
TIMEZONE = ZoneInfo(TIMEZONE_NAME)
SOURCE = "RotoWire"
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 1
REQUEST_DELAY_SECONDS = 0.5
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

MARKETS: dict[str, dict[str, Any]] = {
    "mvp": {
        "display_name": "Most Valuable Player",
        "kind": "player_award",
        "future": "MVP",
        "filename": "mvp.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=MVP",
        "critical": True,
    },
    "dpoy": {
        "display_name": "Defensive Player of the Year",
        "kind": "player_award",
        "future": "Defensive Player",
        "filename": "dpoy.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=Defensive%20Player",
    },
    "roy": {
        "display_name": "Rookie of the Year",
        "kind": "player_award",
        "future": "Rookie",
        "filename": "roy.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=Rookie",
    },
    "mip": {
        "display_name": "Most Improved Player",
        "kind": "player_award",
        "future": "Most Improved Player",
        "filename": "mip.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=Most%20Improved%20Player",
    },
    "sixth_man": {
        "display_name": "Sixth Man of the Year",
        "kind": "player_award",
        "future": "Sixth Man",
        "filename": "sixth_man.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=Sixth%20Man",
    },
    "coy": {
        "display_name": "Coach of the Year",
        "kind": "coach_award",
        "future": "COY",
        "filename": "coy.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/coach-futures.php?future=COY",
    },
    "championship": {
        "display_name": "NBA Championship",
        "kind": "team_winner",
        "future": "Championship",
        "filename": "championship.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/team-futures.php?future=Championship",
        "min_rows": 20,
    },
    "win_totals": {
        "display_name": "NBA Win Totals",
        "kind": "win_total",
        "future": "Win Totals",
        "filename": "win_totals.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/team-futures.php?future=Win%20Totals",
        "min_rows": 20,
    },
    "atlantic_division": {
        "display_name": "Atlantic Division",
        "kind": "team_winner",
        "future": "Atlantic Division",
        "filename": "atlantic_division.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/team-futures.php?future=Atlantic%20Division",
        "expected_teams": {"BOS", "BKN", "NYK", "PHI", "TOR"},
    },
    "central_division": {
        "display_name": "Central Division",
        "kind": "team_winner",
        "future": "Central Division",
        "filename": "central_division.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/team-futures.php?future=Central%20Division",
        "expected_teams": {"CHI", "CLE", "DET", "IND", "MIL"},
    },
    "northwest_division": {
        "display_name": "Northwest Division",
        "kind": "team_winner",
        "future": "Northwest Division",
        "filename": "northwest_division.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/team-futures.php?future=Northwest%20Division",
        "expected_teams": {"DEN", "MIN", "OKC", "POR", "UTA"},
    },
    "southeast_division": {
        "display_name": "Southeast Division",
        "kind": "team_winner",
        "future": "Southeast Division",
        "filename": "southeast_division.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/team-futures.php?future=Southeast%20Division",
        "expected_teams": {"ATL", "CHA", "MIA", "ORL", "WAS"},
    },
    "southwest_division": {
        "display_name": "Southwest Division",
        "kind": "team_winner",
        "future": "Southwest Division",
        "filename": "southwest_division.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/team-futures.php?future=Southwest%20Division",
        "expected_teams": {"DAL", "HOU", "MEM", "NOP", "SAS"},
    },
    "pacific_division": {
        "display_name": "Pacific Division",
        "kind": "team_winner",
        "future": "Pacific Division",
        "filename": "pacific_division.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/team-futures.php?future=Pacific%20Division",
        "expected_teams": {"GSW", "LAC", "LAL", "PHX", "SAC"},
    },
}


class FetchError(RuntimeError):
    """A fetch failure that may still have a known HTTP status."""

    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


def _fetch_json_urllib(endpoint: str) -> tuple[list[dict[str, Any]], bytes, int]:
    """Fetch JSON with urllib, the preferred standard-library client."""
    request = Request(
        endpoint,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                http_status = response.status
                content_type = response.headers.get_content_type()
                raw_body = response.read()
            break
        except HTTPError as exc:
            is_retryable = exc.code == 429 or 500 <= exc.code < 600
            if attempt < MAX_ATTEMPTS and is_retryable:
                print(
                    f"Retrying after HTTP {exc.code} "
                    f"({attempt}/{MAX_ATTEMPTS}): {endpoint}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            raise FetchError(f"HTTP {exc.code}: {exc.reason}", exc.code) from exc
        except (URLError, TimeoutError, IncompleteRead, ConnectionError) as exc:
            if attempt < MAX_ATTEMPTS:
                print(
                    f"Retrying after network read error "
                    f"({attempt}/{MAX_ATTEMPTS}): {endpoint}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            raise FetchError(
                f"Network read failed after {MAX_ATTEMPTS} attempts: {exc}"
            ) from exc

    if not 200 <= http_status < 300:
        raise FetchError(f"Unexpected HTTP status {http_status}", http_status)
    if content_type != "application/json":
        raise FetchError(
            f"Unexpected content type {content_type!r}; expected application/json",
            http_status,
        )
    if not raw_body.strip():
        raise FetchError("Response body is empty", http_status)

    try:
        parsed = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"Response is not valid JSON: {exc}", http_status) from exc

    if not isinstance(parsed, list):
        raise FetchError(
            f"Unexpected JSON root type {type(parsed).__name__}; expected array",
            http_status,
        )

    return parsed, raw_body, http_status


def _fetch_json_curl(endpoint: str) -> tuple[list[dict[str, Any]], bytes, int]:
    """Fallback for RotoWire responses that urllib receives as truncated bodies."""
    with TemporaryDirectory(prefix="nba-futures-") as temp_dir:
        body_path = Path(temp_dir) / "response.json"
        command = [
            "curl",
            "--location",
            "--silent",
            "--show-error",
            "--max-time",
            str(TIMEOUT_SECONDS),
            "--retry",
            "2",
            "--retry-delay",
            "1",
            "--retry-all-errors",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--user-agent",
            USER_AGENT,
            "--header",
            "Accept: application/json",
            "--output",
            str(body_path),
            "--write-out",
            "%{http_code}\n%{content_type}",
            endpoint,
        ]

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS * 3 + 10,
            )
        except FileNotFoundError as exc:
            raise FetchError("curl fallback is unavailable on this system") from exc
        except subprocess.TimeoutExpired as exc:
            raise FetchError("curl fallback timed out") from exc

        metadata_lines = completed.stdout.strip().splitlines()
        http_status: int | None = None
        content_type = ""
        if len(metadata_lines) >= 2:
            try:
                http_status = int(metadata_lines[-2])
            except ValueError:
                http_status = None
            content_type = metadata_lines[-1].split(";", 1)[0].strip()

        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"curl exit code {completed.returncode}"
            raise FetchError(f"curl fallback failed: {detail}", http_status)
        if http_status is None:
            raise FetchError("curl fallback did not report an HTTP status")
        if not 200 <= http_status < 300:
            raise FetchError(f"Unexpected HTTP status {http_status}", http_status)
        if content_type != "application/json":
            raise FetchError(
                f"Unexpected content type {content_type!r}; expected application/json",
                http_status,
            )

        raw_body = body_path.read_bytes()
        if not raw_body.strip():
            raise FetchError("Response body is empty", http_status)

        try:
            parsed = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FetchError(f"Response is not valid JSON: {exc}", http_status) from exc
        if not isinstance(parsed, list):
            raise FetchError(
                f"Unexpected JSON root type {type(parsed).__name__}; expected array",
                http_status,
            )
        return parsed, raw_body, http_status


def fetch_json(endpoint: str) -> tuple[list[dict[str, Any]], bytes, int]:
    """Fetch JSON, falling back to curl only for urllib transport failures."""
    try:
        return _fetch_json_urllib(endpoint)
    except FetchError as exc:
        print(
            f"urllib failed; falling back to curl: {endpoint}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return _fetch_json_curl(endpoint)


def _is_populated(value: Any) -> bool:
    return value not in (None, "")


def _is_number(value: Any) -> bool:
    if not _is_populated(value):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _sportsbook_odds_fields(row: dict[str, Any]) -> list[str]:
    """Return sportsbook odds fields, excluding RotoWire's derived best_odds."""
    return [
        key
        for key in row
        if key.endswith("_odds") and key != "best_odds"
    ]


def _validate_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("JSON array is empty")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("JSON array contains non-object rows")


def _validate_future(market: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    expected_future = market["future"]
    observed_futures = {row.get("future") for row in rows}
    if observed_futures != {expected_future}:
        raise ValueError(
            f"Unexpected future values {sorted(map(str, observed_futures))}; "
            f"expected only {expected_future!r}"
        )


def _validate_identity(rows: list[dict[str, Any]], id_field: str) -> None:
    missing_ids = sum(not _is_populated(row.get(id_field)) for row in rows)
    if missing_ids:
        raise ValueError(f"{missing_ids} rows have no usable {id_field}")

    missing_names = sum(
        not isinstance(row.get("name"), str) or not row["name"].strip() for row in rows
    )
    if missing_names:
        raise ValueError(f"{missing_names} rows have no usable name")


def _validate_sportsbook_odds(rows: list[dict[str, Any]]) -> None:
    populated_odds = sum(
        _is_populated(row[key])
        for row in rows
        for key in _sportsbook_odds_fields(row)
    )
    if populated_odds == 0:
        raise ValueError("No populated sportsbook odds fields were found")


def _validate_team_rows(market: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    _validate_identity(rows, "teamID")

    missing_abbrs = sum(
        not isinstance(row.get("abbr"), str) or not row["abbr"].strip()
        for row in rows
    )
    if missing_abbrs:
        raise ValueError(f"{missing_abbrs} rows have no usable team abbreviation")

    team_ids = [str(row["teamID"]) for row in rows]
    if len(team_ids) != len(set(team_ids)):
        raise ValueError("Duplicate team identities were found")

    minimum_rows = market.get("min_rows")
    if minimum_rows is not None and len(rows) < minimum_rows:
        raise ValueError(
            f"Row count is suspiciously low: {len(rows)}; expected at least {minimum_rows}"
        )

    expected_teams = market.get("expected_teams")
    if expected_teams is not None:
        observed_teams = {row["abbr"] for row in rows}
        if observed_teams != expected_teams:
            raise ValueError(
                f"Unexpected teams {sorted(observed_teams)}; "
                f"expected {sorted(expected_teams)}"
            )


def _is_reasonable_win_total(value: Any) -> bool:
    if not _is_number(value):
        return False
    line = float(value)
    if 1 <= line <= 82:
        return True
    # RotoWire occasionally returns a sportsbook total scaled by 1/1000
    # (for example 0.0305 for 30.5). This affects validation only; the raw
    # response is still saved byte-for-byte without transforming the value.
    return 0.001 <= line <= 0.082


def _validate_win_totals(rows: list[dict[str, Any]]) -> None:
    incomplete_teams: list[str] = []

    for row in rows:
        has_complete_sportsbook = False
        for line_key, line_value in row.items():
            if not line_key.endswith("_line") or line_key == "best_line":
                continue
            sportsbook = line_key[: -len("_line")]
            over_odds = row.get(f"{sportsbook}_odds")
            under_odds = row.get(f"{sportsbook}_odds_under")
            if (
                _is_reasonable_win_total(line_value)
                and _is_number(over_odds)
                and _is_number(under_odds)
            ):
                has_complete_sportsbook = True
                break

        if not has_complete_sportsbook:
            incomplete_teams.append(str(row.get("abbr") or row.get("name") or "?"))

    if incomplete_teams:
        raise ValueError(
            "No sportsbook supplied a complete reasonable line/over/under set for: "
            + ", ".join(incomplete_teams)
        )


def validate_market(market: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    """Validate the stable identity and sportsbook fields for a market kind."""
    _validate_rows(rows)
    _validate_future(market, rows)

    kind = market["kind"]
    if kind == "player_award":
        _validate_identity(rows, "playerID")
        _validate_sportsbook_odds(rows)
    elif kind == "coach_award":
        _validate_identity(rows, "coachID")
        _validate_sportsbook_odds(rows)
    elif kind == "team_winner":
        _validate_team_rows(market, rows)
        _validate_sportsbook_odds(rows)
    elif kind == "win_total":
        _validate_team_rows(market, rows)
        _validate_win_totals(rows)
    else:
        raise ValueError(f"Unknown market kind {kind!r}")

    if market["future"] == "MVP" and len(rows) < 3:
        raise ValueError(f"MVP row count is suspiciously low: {len(rows)}")

    return len(rows)


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)


def save_snapshot(snapshot_dir: Path, filename: str, raw_body: bytes) -> Path:
    """Save the untouched response body for a successfully validated market."""
    output_path = snapshot_dir / filename
    atomic_write(output_path, raw_body)
    return output_path


def save_metadata(snapshot_dir: Path, metadata: dict[str, Any]) -> Path:
    output_path = snapshot_dir / "_meta.json"
    encoded = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    atomic_write(output_path, encoded)
    return output_path


def print_summary(metadata: dict[str, Any]) -> None:
    print("\nMarket                         Status  Rows", flush=True)
    print("-" * 48, flush=True)
    for result in metadata["markets"].values():
        status = "OK" if result["success"] else "FAILED"
        row_count = result["row_count"] if result["row_count"] is not None else "-"
        print(f"{result['market']:<30} {status:<7} {row_count}", flush=True)


def main() -> int:
    now = datetime.now(TIMEZONE)
    snapshot_date = now.date().isoformat()
    repository_root = Path(__file__).resolve().parents[1]
    snapshot_dir = repository_root / "data" / snapshot_date

    metadata: dict[str, Any] = {
        "season": SEASON,
        "snapshot_date": snapshot_date,
        "snapshot_at": now.isoformat(),
        "timezone": TIMEZONE_NAME,
        "source": SOURCE,
        "markets": {},
    }

    successful_markets = 0
    critical_failures: list[str] = []

    market_items = list(MARKETS.items())
    for index, (slug, market) in enumerate(market_items):
        result: dict[str, Any] = {
            "market": market["display_name"],
            "kind": market["kind"],
            "endpoint": market["endpoint"],
            "success": False,
            "http_status": None,
            "row_count": None,
            "file": None,
            "error": None,
        }
        metadata["markets"][slug] = result

        print(f"Fetching {slug}: {market['endpoint']}", flush=True)
        try:
            rows, raw_body, http_status = fetch_json(market["endpoint"])
            result["http_status"] = http_status
            result["row_count"] = len(rows)
            row_count = validate_market(market, rows)
            output_path = save_snapshot(snapshot_dir, market["filename"], raw_body)
            result.update(
                {
                    "success": True,
                    "row_count": row_count,
                    "file": str(output_path.relative_to(repository_root)),
                }
            )
            successful_markets += 1
            print(f"Saved {slug}: {row_count} rows -> {output_path}", flush=True)
        except FetchError as exc:
            result["http_status"] = exc.http_status
            result["error"] = str(exc)
            if market.get("critical"):
                critical_failures.append(slug)
            print(f"FAILED {slug}: {exc}", file=sys.stderr, flush=True)
        except (OSError, ValueError) as exc:
            result["error"] = str(exc)
            if market.get("critical"):
                critical_failures.append(slug)
            print(f"FAILED {slug}: {exc}", file=sys.stderr, flush=True)
        finally:
            if index < len(market_items) - 1:
                time.sleep(REQUEST_DELAY_SECONDS)

    metadata_path = save_metadata(snapshot_dir, metadata)
    print(f"Saved metadata -> {metadata_path}", flush=True)
    print_summary(metadata)

    if successful_markets == 0:
        print("All markets failed; exiting with status 1.", file=sys.stderr)
        return 1
    if critical_failures:
        print(
            f"Critical markets failed: {', '.join(critical_failures)}; "
            "exiting with status 1.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
