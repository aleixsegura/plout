#!/usr/bin/env python3
"""Tests for the DEGIRO session the exporters share (degiro.connect).

A session that is wrongly reused fails the export; one that is wrongly
discarded costs a fresh login — a phone confirmation without a TOTP secret.
These pin which saved sessions are tried and that a dead one always falls back
to a normal login:

    .venv/bin/python -m unittest discover -s tests -v
"""
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from plout.exporters import degiro
from plout.exporters.degiro import SESSION_MAX_AGE_SECONDS, load_session, save_session


class FakeAPI:
    """Stands in for degiro-connector's TradingAPI; `live` decides whether
    get_client_details answers like a valid session."""

    live_sessions: set[str] = set()
    logins = 0

    def __init__(self, credentials):
        self.credentials = credentials
        self.connection_storage = mock.Mock(session_id="")

    def connect(self):
        FakeAPI.logins += 1
        self.connection_storage.session_id = "fresh-session"
        FakeAPI.live_sessions.add("fresh-session")

    def get_client_details(self):
        if self.connection_storage.session_id in FakeAPI.live_sessions:
            return {"data": {"intAccount": 111, "id": 222}}
        return None


class SessionFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".degiro-session.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        save_session("jane", "sid", 111, 222, path=self.path)
        session = load_session("jane", path=self.path)
        self.assertEqual(session["session_id"], "sid")
        self.assertEqual(session["int_account"], 111)
        self.assertEqual(session["user_token"], 222)

    def test_file_is_private(self):
        save_session("jane", "sid", 111, 222, path=self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_other_username_is_ignored(self):
        save_session("jane", "sid", 111, 222, path=self.path)
        self.assertIsNone(load_session("john", path=self.path))

    def test_stale_session_is_ignored(self):
        save_session("jane", "sid", 111, 222, path=self.path)
        later = time.time() + SESSION_MAX_AGE_SECONDS + 1
        self.assertIsNone(load_session("jane", path=self.path, now=later))

    def test_missing_or_corrupt_file_is_ignored(self):
        self.assertIsNone(load_session("jane", path=self.path))
        self.path.write_text("{not json")
        self.assertIsNone(load_session("jane", path=self.path))
        self.path.write_text(json.dumps({"username": "jane"}))
        self.assertIsNone(load_session("jane", path=self.path))


class ConnectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".degiro-session.json"
        FakeAPI.live_sessions = set()
        FakeAPI.logins = 0
        env = {"DEGIRO_USERNAME": "jane", "DEGIRO_PASSWORD": "pw"}
        self.patches = [
            mock.patch.object(degiro, "TradingAPI", FakeAPI),
            mock.patch.object(degiro, "SESSION_FILE", self.path),
            mock.patch.object(degiro, "get_env", env.get),
            # load/save default their path at definition time, so route them
            # to the temp file explicitly.
            mock.patch.object(degiro, "load_session",
                              lambda u: load_session(u, path=self.path)),
            mock.patch.object(degiro, "save_session",
                              lambda *a: save_session(*a, path=self.path)),
            # connect() announces a reused session on stderr.
            redirect_stderr(io.StringIO()),
        ]
        for p in self.patches:
            p.__enter__()

    def tearDown(self):
        for p in reversed(self.patches):
            p.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_logs_in_and_saves_the_session(self):
        api, token = degiro.connect()
        self.assertEqual(FakeAPI.logins, 1)
        self.assertEqual(token, 222)
        self.assertEqual(api.credentials.int_account, 111)
        self.assertEqual(load_session("jane", path=self.path)["session_id"], "fresh-session")

    def test_second_export_reuses_the_session(self):
        degiro.connect()
        api, token = degiro.connect()
        self.assertEqual(FakeAPI.logins, 1)
        self.assertEqual(token, 222)
        self.assertEqual(api.connection_storage.session_id, "fresh-session")

    def test_dead_session_falls_back_to_login(self):
        save_session("jane", "expired-session", 111, 222, path=self.path)
        api, _ = degiro.connect()
        self.assertEqual(FakeAPI.logins, 1)
        self.assertEqual(api.connection_storage.session_id, "fresh-session")

    def test_totp_secret_reaches_the_credentials(self):
        env = {"DEGIRO_USERNAME": "jane", "DEGIRO_PASSWORD": "pw",
               "DEGIRO_TOTP_SECRET": "JBSW Y3DP EHPK 3PXP"}
        with mock.patch.object(degiro, "get_env", env.get), \
                mock.patch.object(degiro, "_wait_for_fresh_totp_window"):
            api, _ = degiro.connect()
        self.assertEqual(api.credentials.totp_secret_key, "JBSWY3DPEHPK3PXP")


if __name__ == "__main__":
    unittest.main()
