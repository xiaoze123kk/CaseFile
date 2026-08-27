"""Generate the committed FastAPI OpenAPI snapshot deterministically."""

from __future__ import annotations

import json
from pathlib import Path

from casefile.api.app import create_app


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    output = repo_root / "contracts" / "openapi.json"
    document = create_app(verify_database=False).openapi()
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Generated {output.relative_to(repo_root).as_posix()}")


if __name__ == "__main__":
    main()
