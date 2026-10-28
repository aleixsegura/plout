#!/usr/bin/env python3
"""Tests for the one-shot refresh (plout sync): which exporters run, in what
order, and that one failure never stops the rest — except DEGIRO steps after a
failed DEGIRO login, which would each ask the phone again:

    .venv/bin/python -m unittest discover -s tests -v
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from plout.sync import run, select_steps


def names(steps):
    return [s.name for s in steps]


class SelectStepsTest(unittest.TestCase):
    def test_defaults_leave_taxes_out_and_start_with_revolut(self):
        self.assertEqual(
            names(select_steps()),
            ["revolut", "degiro", "deposits", "dividends", "fees", "prices", "marketcaps"],
        )

    def test_taxes_flag_runs_it_before_the_keyless_exports(self):
        steps = names(select_steps(taxes=True))
        self.assertIn("taxes", steps)
        self.assertLess(steps.index("taxes"), steps.index("prices"))

    def test_only_and_skip(self):
        self.assertEqual(names(select_steps(only="dividends,degiro")), ["degiro", "dividends"])
        self.assertEqual(names(select_steps(only="taxes")), ["taxes"])
        self.assertNotIn("revolut", names(select_steps(skip="revolut")))

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(SystemExit):
            select_steps(only="degrio")


class RunTest(unittest.TestCase):
    def test_a_failure_does_not_stop_the_rest(self):
        ran = []

        def runner(step, _out):
            ran.append(step.name)
            return 1 if step.name == "revolut" else 0

        with redirect_stdout(io.StringIO()):
            results = run(select_steps(), "/tmp/out", runner=runner)
        self.assertEqual(ran, names(select_steps()))
        self.assertEqual(results[0][1], "failed")
        self.assertTrue(all(status == "ok" for _, status, _ in results[1:]))

    def test_failed_degiro_login_skips_the_other_degiro_steps(self):
        ran = []

        def runner(step, _out):
            ran.append(step.name)
            return 1 if step.name == "degiro" else 0

        with redirect_stdout(io.StringIO()):
            results = run(select_steps(), "/tmp/out", runner=runner)
        self.assertEqual(ran, ["revolut", "degiro", "prices", "marketcaps"])
        status = {s.name: st for s, st, _ in results}
        self.assertEqual(status["deposits"], "skipped")
        self.assertEqual(status["fees"], "skipped")


if __name__ == "__main__":
    unittest.main()
