"""Offline configuration check. It never calls an external service."""
from __future__ import annotations

from pathlib import Path

import yaml

from kvlib import connections, paths


REQUIRED_SECRETS = ("NOTION_TOKEN", "SLACK_BOT_TOKEN", "GMAIL_TOKEN_FILE", "GOOGLE_CREDENTIALS_FILE")


def _env(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def main() -> int:
    errors = []
    env_path = paths.config_root() / ".env"
    conn_path = connections.path()
    people_path = paths.config_root() / "people.yaml"
    deployment_path = paths.config_root() / "deployment.yaml"
    env = _env(env_path)
    if not env_path.exists():
        errors.append(f"missing {env_path}; run ./kv setup")
    if not conn_path.exists():
        errors.append(f"missing {conn_path}; run ./kv setup")
    if not people_path.exists():
        errors.append(f"missing {people_path}; run ./kv setup")
    if not deployment_path.exists():
        errors.append(f"missing {deployment_path}; run ./kv setup")
    for key in REQUIRED_SECRETS:
        if not env.get(key):
            errors.append(f"empty secret setting: {key}")
    if conn_path.exists():
        raw = yaml.safe_load(conn_path.read_text(encoding="utf-8")) or {}
        flat = str(raw)
        if "EXAMPLE" in flat or "00000000-0000-0000-0000-" in flat or "C00000000" in flat:
            errors.append("connections.yaml still contains example identifiers")
    if people_path.exists() and "example.vc" in people_path.read_text(encoding="utf-8"):
        errors.append("people.yaml still contains the example reviewer")
    for key in ("GMAIL_TOKEN_FILE", "GOOGLE_CREDENTIALS_FILE"):
        value = env.get(key)
        if value and not Path(value).expanduser().exists():
            errors.append(f"file does not exist: {key}={value}")
    if errors:
        print("configuration is not ready:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"configuration ready: {conn_path}")
    print("outbound gates remain closed until you explicitly change deployment.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
