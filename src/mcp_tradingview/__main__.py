"""Module entrypoint so the server can be launched with ``python -m mcp_tradingview``.

This is the most robust way to start the server from Claude Desktop on Windows:
it does not rely on the ``mcp-tradingview`` console script being on ``PATH``.
"""

from __future__ import annotations

from mcp_tradingview.server import main

if __name__ == "__main__":
    main()
