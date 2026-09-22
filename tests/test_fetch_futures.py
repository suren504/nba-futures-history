import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import fetch_futures as fetch


class SnapshotFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.today = fetch.datetime.now(fetch.TIMEZONE).date().isoformat()
        self.markets = {slug: fetch.MARKETS[slug] for slug in ("mvp", "roy")}

    def rows(self, slug):
        return [
            {"future": self.markets[slug]["future"], "playerID": str(index),
             "name": f"Player {index}", "book_odds": "+500"}
            for index in range(3)
        ]

    def history(self, day="2026-08-29", season=fetch.SEASON):
        directory = self.root / "data" / day
        fetch.save_snapshot(directory, "mvp.json", json.dumps(self.rows("mvp")).encode())
        fetch.save_metadata(directory, {
            "season": season, "markets": {"mvp": {"success": True}},
        })

    def run_fetch(self, empty=("mvp",), broken=False):
        def response(endpoint):
            if broken:
                raise fetch.FetchError("HTTP 503", 503)
            slug = next(s for s, m in self.markets.items() if m["endpoint"] == endpoint)
            rows = [] if slug in empty else self.rows(slug)
            return rows, json.dumps(rows).encode(), 200

        with patch.object(fetch, "__file__", str(self.root / "scripts/fetch_futures.py")), \
             patch.object(fetch, "MARKETS", self.markets), \
             patch.object(fetch, "REQUEST_DELAY_SECONDS", 0), \
             patch.object(fetch, "fetch_json", side_effect=response), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            return fetch.main()

    def metadata(self):
        return json.loads((self.root / "data" / self.today / "_meta.json").read_bytes())

    def test_partial_fetch_carries_legacy_data_and_preserves_date_on_rerun(self):
        self.history()
        for _ in range(2):
            self.assertEqual(self.run_fetch(), 0)
            market = self.metadata()["markets"]["mvp"]
            self.assertFalse(market["success"])
            self.assertTrue(market["stale"])
            self.assertEqual(market["source_date"], "2026-08-29")
            self.assertEqual((self.root / market["file"]).read_bytes(),
                             (self.root / "data/2026-08-29/mvp.json").read_bytes())

    def test_recovery_marks_unchanged_odds_fresh(self):
        self.history()
        self.run_fetch()
        self.assertEqual(self.run_fetch(empty=()), 0)
        market = self.metadata()["markets"]["mvp"]
        self.assertTrue(market["success"])
        self.assertFalse(market["stale"])
        self.assertEqual(market["source_date"], self.today)
        self.assertIsNone(market["error"])

    def test_all_empty_leaves_existing_files_untouched(self):
        self.history()
        self.run_fetch()
        before = {p: p.read_bytes() for p in self.root.rglob("*.json")}
        self.assertEqual(self.run_fetch(empty=("mvp", "roy")), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*.json")})

    def test_all_network_failures_still_fail_without_writes(self):
        self.assertEqual(self.run_fetch(broken=True), 1)
        self.assertFalse((self.root / "data").exists())

    def test_missing_history_does_not_invent_odds(self):
        self.assertEqual(self.run_fetch(), 0)
        market = self.metadata()["markets"]["mvp"]
        self.assertIsNone(market["file"])
        self.assertFalse(market["stale"])

    def test_other_season_and_invalid_latest_snapshot_are_skipped(self):
        self.history()
        self.history("2026-08-30", season="2025-26")
        self.history("2026-08-31")
        (self.root / "data/2026-08-31/mvp.json").write_text("[]")
        self.assertEqual(self.run_fetch(), 0)
        self.assertEqual(self.metadata()["markets"]["mvp"]["source_date"], "2026-08-29")


if __name__ == "__main__":
    unittest.main()
