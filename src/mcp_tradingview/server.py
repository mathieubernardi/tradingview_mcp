"""MCP TradingView Server — supports stdio and SSE transports."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import mcp.server.stdio
import uvicorn
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import (
    TextContent,
    Tool,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from mcp_tradingview.client import TradingViewClient
from mcp_tradingview.config import TransportMode, settings
from mcp_tradingview.tools import build_tools, handle_tool_call

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

logging.basicConfig(
    level=settings.log_level.value,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Server factory
# ─────────────────────────────────────────────


def create_mcp_server(tv_client: TradingViewClient) -> Server:
    """Build and configure the MCP Server instance."""
    server: Server = Server("mcp-tradingview")

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        return build_tools()

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        return await handle_tool_call(name, arguments, tv_client)

    return server


# ─────────────────────────────────────────────
# STDIO transport
# ─────────────────────────────────────────────


async def run_stdio(tv_client: TradingViewClient) -> None:
    """Run server over stdin/stdout (for Claude Desktop)."""
    logger.info("Starting MCP TradingView server — stdio transport")
    server = create_mcp_server(tv_client)

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


# ─────────────────────────────────────────────
# SSE transport
# ─────────────────────────────────────────────


def create_sse_app(tv_client: TradingViewClient) -> Starlette:
    """Create a Starlette ASGI app with SSE transport."""
    sse_transport = SseServerTransport("/messages/")
    server = create_mcp_server(tv_client)

    async def handle_sse(request: Request) -> Response:
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )
        return Response()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await tv_client.connect()
        logger.info("TradingView client connected")
        try:
            yield
        finally:
            await tv_client.disconnect()
            logger.info("TradingView client disconnected")

    return Starlette(
        debug=False,
        lifespan=lifespan,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
            Route("/health", endpoint=lambda r: Response("ok")),
        ],
    )


async def run_sse(tv_client: TradingViewClient) -> None:
    """Run server as HTTP/SSE (for remote Claude or claude.ai)."""
    logger.info(
        "Starting MCP TradingView server — SSE transport on %s:%d",
        settings.host,
        settings.port,
    )
    app = create_sse_app(tv_client)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.value.lower(),
    )
    server = uvicorn.Server(config)
    await server.serve()


# ─────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────


async def _async_main() -> None:
    tv_client = TradingViewClient()

    if settings.transport == TransportMode.STDIO:
        # Do NOT connect before serving: the TradingView session is established
        # lazily on the first tool call so the MCP initialize handshake returns
        # immediately and Claude Desktop does not time out at startup.
        try:
            await run_stdio(tv_client)
        finally:
            await tv_client.disconnect()
    else:
        # SSE manages connect/disconnect via lifespan
        await run_sse(tv_client)


def main() -> None:
    """CLI entrypoint."""
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
