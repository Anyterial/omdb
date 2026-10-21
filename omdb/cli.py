"""Run the combined OMDB website and OPTIMADE service."""

import argparse
from pathlib import Path

from httk.serve.web.runtime.devserver import run_dev_server

from .app import create_app


def main(argv: list[str] | None = None) -> int:
    """Serve a rebuilt database.

    :param argv: Command arguments, or ``None`` for process arguments.
    :return: Exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="built data directory (default: OMDB_DATA_DIR or data/omdb)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        app = create_app(args.database)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    run_dev_server(app=app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
