"""Run the real workbench against an isolated QA database and file store."""

import argparse
from pathlib import Path

import uvicorn

from backend.config.settings import load_settings
from backend.interfaces.api.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    root = Path(args.workdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    settings.model.thinking_mode = "none"
    settings.database.url = "sqlite:///" + (root / "workbench.db").as_posix()
    settings.materials.storage_root = str(root / "uploads")
    settings.materials.cache_root = str(root / "parser-cache")
    settings.application["run_store_path"] = str(root / "runs.json")
    uvicorn.run(
        create_app(settings=settings),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
