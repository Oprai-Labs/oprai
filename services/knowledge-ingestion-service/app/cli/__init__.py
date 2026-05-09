"""CLI entry point: python -m app.cli <command>"""

import asyncio
import sys

from app.cli.seed import cmd_ingest_seed


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.cli <command> [args...]")
        print("Commands:")
        print("  ingest-seed <directory>   Ingest markdown/text files from a directory")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "ingest-seed":
        directory = sys.argv[2] if len(sys.argv) > 2 else "./eval/seed_docs"
        asyncio.run(cmd_ingest_seed(directory))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
