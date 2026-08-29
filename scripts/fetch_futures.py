#!/usr/bin/env python3
"""Archive daily raw RotoWire NBA futures and awards JSON snapshots."""

from __future__ import annotations

import json
import subprocess
import sys
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
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

MARKETS: dict[str, dict[str, str]] = {
    "mvp": {
        "display_name": "Most Valuable Player",
        "future": "MVP",
        "filename": "mvp.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=MVP",
    },
    "dpoy": {
        "display_name": "Defensive Player of the Year",
        "future": "Defensive Player",
        "filename": "dpoy.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=Defensive%20Player",
    },
    "roy": {
        "display_name": "Rookie of the Year",
        "future": "Rookie",
        "filename": "roy.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=Rookie",
    },
    "mip": {
        "display_name": "Most Improved Player",
        "future": "Most Improved Player",
        "filename": "mip.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=Most%20Improved%20Player",
    },
    "sixth_man": {
        "display_name": "Sixth Man of the Year",
        "future": "Sixth Man",
        "filename": "sixth_man.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/player-futures.php?future=Sixth%20Man",
    },
    "coy": {
        "display_name": "Coach of the Year",
        "future": "COY",
        "filename": "coy.json",
        "endpoint": "https://www.rotowire.com/betting/nba/tables/coach-futures.php?future=COY",
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


def validate_market(market: dict[str, str], rows: list[dict[str, Any]]) -> int:
    """Validate award identity and a minimal, stable part of the response structure."""
    if not rows:
        raise ValueError("JSON array is empty")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("JSON array contains non-object rows")

    expected_future = market["future"]
    observed_futures = {row.get("future") for row in rows}
    if observed_futures != {expected_future}:
        raise ValueError(
            f"Unexpected future values {sorted(map(str, observed_futures))}; "
            f"expected only {expected_future!r}"
        )

    missing_names = sum(
        not isinstance(row.get("name"), str) or not row["name"].strip() for row in rows
    )
    if missing_names:
        raise ValueError(f"{missing_names} rows have no usable name")

    populated_odds = sum(
        value not in (None, "")
        for row in rows
        for key, value in row.items()
        if key.endswith("_odds")
    )
    if populated_odds == 0:
        raise ValueError("No populated sportsbook odds fields were found")

    if expected_future == "MVP" and len(rows) < 3:
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


def main() -> int:
    now = datetime.now(TIMEZONE)
    snapshot_date = now.date().isoformat()
    repository_root = Path(__file__).resolve().parents[1]
    snapshot_dir = repository_root / "data" / SEASON / snapshot_date

    metadata: dict[str, Any] = {
        "season": SEASON,
        "snapshot_date": snapshot_date,
        "snapshot_at": now.isoformat(),
        "timezone": TIMEZONE_NAME,
        "source": SOURCE,
        "markets": {},
    }

    successful_markets = 0
    mvp_succeeded = False

    for slug, market in MARKETS.items():
        result: dict[str, Any] = {
            "market": market["display_name"],
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
            mvp_succeeded = mvp_succeeded or slug == "mvp"
            print(f"Saved {slug}: {row_count} rows -> {output_path}", flush=True)
        except FetchError as exc:
            result["http_status"] = exc.http_status
            result["error"] = str(exc)
            print(f"FAILED {slug}: {exc}", file=sys.stderr, flush=True)
        except (OSError, ValueError) as exc:
            result["error"] = str(exc)
            print(f"FAILED {slug}: {exc}", file=sys.stderr, flush=True)

    metadata_path = save_metadata(snapshot_dir, metadata)
    print(f"Saved metadata -> {metadata_path}", flush=True)

    if successful_markets == 0:
        print("All markets failed; exiting with status 1.", file=sys.stderr)
        return 1
    if not mvp_succeeded:
        print("MVP failed validation or retrieval; exiting with status 1.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
