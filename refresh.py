#!/usr/bin/env python3
"""
Refresh every export in one go.

Runs the exporters one after another, each as its own process exactly as if it
had been started by hand, keeps going when one fails, and prints a summary.

Revolut runs first because it is the only step that may need you: its browser
window opens and, if the session has expired, waits for the login. Everything
after it runs unattended. The DEGIRO exporters share one session (see
export_degiro.SESSION_FILE), so the batch logs in to DEGIRO once — and with
DEGIRO_TOTP_SECRET in .env, without the phone.

export_taxes.py is left out unless --taxes is passed: it is a once-a-year job
and refuses to run without its manual inputs.

Examples:
  python3 refresh.py
  python3 refresh.py --taxes
  python3 refresh.py --skip revolut
  python3 refresh.py --only degiro,dividends
"""

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from common import SCRIPT_DIR

# Same freshness window the dev server uses for the keyless exports.
KEYLESS_MAX_AGE_HOURS = "12"


@dataclass(frozen=True)
class Step:
    name: str
    script: str
    args: tuple[str, ...] = ()
    degiro: bool = False  # logs in to DEGIRO
    default: bool = True


STEPS: tuple[Step, ...] = (
    Step("revolut", "export_revolut.py"),
    Step("degiro", "export_degiro.py", degiro=True),
    Step("deposits", "export_deposits.py", degiro=True),
    Step("dividends", "export_dividends.py", degiro=True),
    Step("fees", "export_fees.py", degiro=True),
    Step("taxes", "export_taxes.py", degiro=True, default=False),
    # After the brokers: both read the holdings the steps above just wrote.
    Step("prices", "export_prices.py", ("--max-age-hours", KEYLESS_MAX_AGE_HOURS)),
    Step("marketcaps", "export_marketcaps.py", ("--max-age-hours", KEYLESS_MAX_AGE_HOURS)),
)
STEP_NAMES = tuple(s.name for s in STEPS)


def _names(value: str | None) -> set[str]:
    if not value:
        return set()
    names = {n.strip() for n in value.split(",") if n.strip()}
    unknown = sorted(names - set(STEP_NAMES))
    if unknown:
        raise SystemExit(f"Unknown step(s): {', '.join(unknown)}. Steps: {', '.join(STEP_NAMES)}")
    return names


def select_steps(only: str | None = None, skip: str | None = None, taxes: bool = False) -> list[Step]:
    """The steps to run, in STEPS order. --only overrides the defaults (so it
    can name taxes too); --skip and --taxes adjust them."""
    only_names, skip_names = _names(only), _names(skip)
    selected = []
    for step in STEPS:
        if only_names:
            wanted = step.name in only_names
        else:
            wanted = step.default or (step.name == "taxes" and taxes)
        if wanted and step.name not in skip_names:
            selected.append(step)
    return selected


def run_step(step: Step, output_dir: str) -> int:
    cmd = [sys.executable, str(SCRIPT_DIR / step.script), "--output-dir", output_dir, *step.args]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return subprocess.run(cmd, cwd=SCRIPT_DIR, env=env).returncode


def run(steps: list[Step], output_dir: str, runner=run_step) -> list[tuple[Step, str, float]]:
    """Run each step and return (step, "ok" | "failed" | "skipped", seconds)."""
    results = []
    degiro_failed = False
    for step in steps:
        if step.degiro and degiro_failed:
            # Each DEGIRO exporter would try its own login — and, without a
            # TOTP secret, ask the phone again for every one of them.
            results.append((step, "skipped", 0.0))
            continue
        rule = "─" * max(0, 50 - len(step.name) - len(step.script))
        print(f"\n── {step.name} ({step.script}) {rule}", flush=True)
        started = time.monotonic()
        try:
            code = runner(step, output_dir)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"  could not run {step.script}: {exc}", file=sys.stderr)
            code = 1
        status = "ok" if code == 0 else "failed"
        if step.degiro and status == "failed":
            degiro_failed = True
        results.append((step, status, time.monotonic() - started))
    return results


def print_summary(results: list[tuple[Step, str, float]]) -> None:
    marks = {"ok": "✓", "failed": "✗", "skipped": "–"}
    print("\n── summary " + "─" * 45)
    for step, status, seconds in results:
        note = "  (DEGIRO login failed earlier)" if status == "skipped" else ""
        print(f"  {marks[status]} {step.name:<11} {status:<8} {seconds:6.1f}s{note}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--taxes", action="store_true", help="Also run export_taxes.py")
    parser.add_argument("--only", help=f"Comma-separated steps to run instead of the defaults ({', '.join(STEP_NAMES)})")
    parser.add_argument("--skip", help="Comma-separated steps to leave out")
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory containing the data/ folder, passed to every exporter (default: current dir)",
    )
    args = parser.parse_args()

    steps = select_steps(args.only, args.skip, args.taxes)
    if not steps:
        raise SystemExit("Nothing to run.")
    output_dir = os.path.abspath(args.output_dir)
    results = run(steps, output_dir)
    print_summary(results)
    if any(status != "ok" for _, status, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
