from __future__ import annotations

import logging
import os

from quick_search import mcp


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("QUICK_SEARCH_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    _configure_logging()
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
