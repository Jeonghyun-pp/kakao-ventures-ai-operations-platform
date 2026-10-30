"""Create local configuration files without overwriting existing credentials."""
from __future__ import annotations

import shutil

from kvlib import paths


def main() -> int:
    root = paths.repo_root()
    target = paths.config_root()
    target.mkdir(parents=True, exist_ok=True)
    made = []
    for source, name in ((root / ".env.example", ".env"),
                         (root / "connections.example.yaml", "connections.yaml"),
                         (root / "deployment.example.yaml", "deployment.yaml"),
                         (root / "people.example.yaml", "people.yaml")):
        dest = target / name
        if dest.exists():
            print(f"keep  {dest}")
            continue
        shutil.copyfile(source, dest)
        if name == ".env":
            dest.chmod(0o600)
        made.append(dest)
        print(f"create {dest}")
    print("\nEdit the files above, then run: ./kv doctor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
