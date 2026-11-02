"""Offline configuration check. It never calls an external service."""
from __future__ import annotations

from pathlib import Path

import yaml

from kvlib import connections, constraints, paths


REQUIRED_SECRETS = ("NOTION_TOKEN", "SLACK_BOT_TOKEN", "GMAIL_TOKEN_FILE", "GOOGLE_CREDENTIALS_FILE")


def _env(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _yaml(path: Path, errors: list[str]):
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if value is None:
            errors.append(f"empty YAML: {path}")
        return value
    except (OSError, yaml.YAMLError):
        errors.append(f"cannot read valid YAML: {path}; run ./kv setup for missing files")
        return None


def _connection_errors(data, example, prefix="") -> list[str]:
    errors = []
    if not isinstance(data, dict):
        return [f"connections.yaml: {prefix or 'root'} must be a mapping"]
    for key, expected in example.items():
        name = f"{prefix}.{key}" if prefix else key
        value = data.get(key)
        if isinstance(expected, dict):
            errors += _connection_errors(value, expected, name)
        elif (isinstance(expected, list) and not (
                isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value))
              or isinstance(expected, str) and not (isinstance(value, str) and value.strip())):
            errors.append(f"connections.yaml: missing or invalid {name}")
        elif value == expected or any(mark in str(value) for mark in ("EXAMPLE", "example.vc", "00000000-0000", "C00000000")):
            # Organization name, reviewer name and worksheet name may legitimately match the example.
            if key not in ("name", "sender_name", "assignee", "worksheet_name", "gmail_label", "notification_company_name"):
                errors.append(f"connections.yaml: replace example value for {name}")
    return errors


def main() -> int:
    errors = []
    root = paths.config_root()
    env_path = root / ".env"
    try:
        env = _env(env_path)
        if not env_path.is_file():
            errors.append(f"missing {env_path}; run ./kv setup")
        elif env_path.stat().st_mode & 0o077:
            errors.append(f"restrict permissions: chmod 600 {env_path}")
    except OSError:
        env = {}
        errors.append(f"cannot read {env_path}")
    for key in REQUIRED_SECRETS:
        if not env.get(key):
            errors.append(f"empty secret setting: {key}")
    for key in ("GMAIL_TOKEN_FILE", "GOOGLE_CREDENTIALS_FILE"):
        value = env.get(key)
        if value and not Path(value).expanduser().is_file():
            errors.append(f"credential file does not exist: {key}")

    raw = _yaml(connections.path(), errors)
    if raw is not None:
        example = yaml.safe_load(connections.EXAMPLE.read_text(encoding="utf-8"))
        errors += _connection_errors(raw, example)
    roster = _yaml(root / "people.yaml", errors)
    if not (isinstance(roster, list) and all(
            isinstance(row, dict) and isinstance(row.get("names"), list) and row["names"]
            and all(isinstance(name, str) and name.strip() for name in row["names"])
            and isinstance(row.get("email", ""), str)
            and isinstance(row.get("slack_id", ""), str) for row in roster)):
        errors.append("people.yaml: expected reviewer list with names; email and slack_id must be strings when present")
    elif "example.vc" in str(roster) or "U00000000" in str(roster):
        errors.append("people.yaml still contains example reviewers")

    if not (root / "deployment.yaml").is_file():
        errors.append("missing deployment.yaml; rollout switches default closed; run ./kv setup")
    constraints._deployment.cache_clear()
    active = []
    try:
        for task, settings in constraints._deployment().items():
            for key, value in settings.items():
                dotted = f"{task}.{key}"
                default = constraints.rollout_default(dotted)
                if default is not None and value != default:
                    active.append(dotted)
    except (OSError, ValueError, yaml.YAMLError):
        errors.append("deployment.yaml: invalid workflow mapping or rollout value; compare deployment.example.yaml")
    if errors:
        print("configuration is not ready:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"local configuration checks passed: {connections.path()}")
    print("enabled rollout settings: " + (", ".join(sorted(active)) or "none (closed)"))
    print("Offline check only: remote credentials, permissions and model tools were not verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
