"""
The `plout` command: one entry point for the sync and every exporter.

  plout sync [--taxes]       refresh everything (see plout.sync)
  plout <exporter> [args]    run one exporter, e.g. `plout degiro --logout`
  plout <command> --help     that command's own options

Each command runs its module exactly as `python -m <module>` would, so the
modules keep their own argument parsing and error messages.
"""

import runpy
import sys

# Command → module. Order is the order `plout --help` lists them in.
COMMANDS: dict[str, tuple[str, str]] = {
    "sync": ("plout.sync", "refresh every export in one go"),
    "revolut": ("plout.exporters.revolut", "Revolut balances, trades and transactions"),
    "degiro": ("plout.exporters.degiro", "DEGIRO positions"),
    "deposits": ("plout.exporters.deposits", "DEGIRO deposits and withdrawals"),
    "dividends": ("plout.exporters.dividends", "DEGIRO dividends"),
    "fees": ("plout.exporters.fees", "DEGIRO fees"),
    "taxes": ("plout.exporters.taxes", "the tax return (FIFO gains, filing pack)"),
    "prices": ("plout.exporters.prices", "daily price history, in EUR"),
    "marketcaps": ("plout.exporters.marketcaps", "market capitalisations"),
    "history": ("plout.history", "backfill the running totals in history.json"),
}


def usage() -> str:
    width = max(map(len, COMMANDS))
    lines = ["usage: plout <command> [args]", "", "commands:"]
    lines += [f"  {name:<{width}}  {summary}" for name, (_, summary) in COMMANDS.items()]
    lines += ["", "Run `plout <command> --help` for a command's options."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("-h", "--help"):
        print(usage())
        return
    command, rest = args[0], args[1:]
    if command not in COMMANDS:
        sys.exit(f"plout: unknown command {command!r}\n\n{usage()}")
    # argparse takes its `prog` from argv[0], so help reads "usage: plout degiro …".
    sys.argv = [f"plout {command}", *rest]
    runpy.run_module(COMMANDS[command][0], run_name="__main__")


if __name__ == "__main__":
    main()
