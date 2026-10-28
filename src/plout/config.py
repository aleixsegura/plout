"""Paths and .env loading shared by the exporters."""

from pathlib import Path

# src/plout/config.py → the repository root, which holds .env, data/, the
# manual inputs and the broker sessions. The package is meant to be installed
# editable (`pip install -e .`) so this keeps pointing at the checkout.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env
