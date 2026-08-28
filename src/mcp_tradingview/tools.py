"""MCP tool handlers for TradingView."""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.types import TextContent, Tool

from mcp_tradingview.client import TradingViewClient
from mcp_tradingview.models import (
    GetHistoricalDataInput,
    GetIndicatorsInput,
    GetQuoteInput,
    Interval,
    ScreenerInput,
    ScreenerMarket,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Tool definitions (JSON Schema)
# ─────────────────────────────────────────────

INTERVAL_ENUM = [e.value for e in Interval]
MARKET_ENUM = [e.value for e in ScreenerMarket]


def build_tools() -> list[Tool]:
    """Return the list of MCP tools exposed by this server."""
    return [
        Tool(
            name="tv_get_historical_data",
            description=(
                "Fetch historical OHLCV (Open, High, Low, Close, Volume) bars "
                "for any symbol from TradingView. Supports stocks, crypto, forex, futures."
            ),
            inputSchema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker symbol, e.g. 'BTCUSDT', 'AAPL', 'EURUSD'",
                    },
                    "exchange": {
                        "type": "string",
                        "default": "EURONEXT",
                        "description": (
                            "Exchange name, e.g. 'EURONEXT' (PEA / Paris), 'NASDAQ', "
                            "'NYSE', 'BINANCE', 'FX'"
                        ),
                    },
                    "interval": {
                        "type": "string",
                        "enum": INTERVAL_ENUM,
                        "default": "1D",
                        "description": (
                            "Chart interval: 1, 3, 5, 15, 30, 45 (min) "
                            "| 1H, 2H, 3H, 4H | 1D, 1W, 1M"
                        ),
                    },
                    "n_bars": {
                        "type": "integer",
                        "default": 500,
                        "minimum": 10,
                        "maximum": 5000,
                        "description": "Number of bars to return",
                    },
                },
            },
        ),
        Tool(
            name="tv_get_quote",
            description=(
                "Get the latest price quote for a symbol: last OHLCV bar "
                "plus percentage change from the previous bar."
            ),
            inputSchema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker symbol"},
                    "exchange": {"type": "string", "default": "EURONEXT"},
                    "interval": {
                        "type": "string",
                        "enum": INTERVAL_ENUM,
                        "default": "1D",
                    },
                },
            },
        ),
        Tool(
            name="tv_get_indicators",
            description=(
                "Compute technical indicators for any symbol. Includes RSI, MACD, "
                "Bollinger Bands (+ squeeze detection), EMA, SMA, ATR (+ stop levels), "
                "Stochastic, ADX (+DI/-DI), OBV, volume MA20/ratio, RSI divergence flags. "
                "Optional: weekly multi-timeframe context (include_weekly=true), "
                "relative strength vs benchmark (benchmark_symbol), "
                "composite technical score [-1,+1] (include_score=true)."
            ),
            inputSchema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {"type": "string"},
                    "exchange": {"type": "string", "default": "EURONEXT"},
                    "interval": {
                        "type": "string",
                        "enum": INTERVAL_ENUM,
                        "default": "1D",
                    },
                    "n_bars": {"type": "integer", "default": 200, "minimum": 50},
                    "indicators": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "rsi", "macd", "bbands", "ema", "sma",
                                "atr", "stoch", "adx", "obv", "all",
                            ],
                        },
                        "default": ["all"],
                    },
                    "rsi_length": {"type": "integer", "default": 14},
                    "ema_periods": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "default": [20, 50, 200],
                    },
                    "sma_periods": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "default": [20, 50, 200],
                    },
                    "macd_fast": {"type": "integer", "default": 12},
                    "macd_slow": {"type": "integer", "default": 26},
                    "macd_signal": {"type": "integer", "default": 9},
                    "bb_length": {"type": "integer", "default": 20},
                    "bb_std": {"type": "number", "default": 2.0},
                    "atr_length": {"type": "integer", "default": 14},
                    "atr_stop_mult": {
                        "type": "number",
                        "default": 2.0,
                        "description": "ATR multiplier for stop_long / stop_short levels",
                    },
                    "divergence_lookback": {
                        "type": "integer",
                        "default": 20,
                        "minimum": 10,
                        "maximum": 50,
                        "description": "Bars window for RSI divergence detection",
                    },
                    "benchmark_symbol": {
                        "type": "string",
                        "default": "",
                        "description": "Benchmark ticker for RS 1M/3M (e.g. 'CW8'). Empty = skip.",
                    },
                    "benchmark_exchange": {"type": "string", "default": "EURONEXT"},
                    "include_weekly": {
                        "type": "boolean",
                        "default": False,
                        "description": "Fetch weekly bars for weekly_rsi, weekly_adx, weekly_trend",
                    },
                    "include_score": {
                        "type": "boolean",
                        "default": False,
                        "description": "Compute composite technical_score [-1,+1] and sub-scores",
                    },
                },
            },
        ),
        Tool(
            name="tv_screener",
            description=(
                "Screen stocks, crypto or forex symbols by market, volume, RSI range. "
                "Markets include 'france' (Euronext Paris — PEA) and 'america' "
                "(Nasdaq / NYSE). Returns top matches with price, volume and RSI data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "market": {
                        "type": "string",
                        "enum": MARKET_ENUM,
                        "default": "france",
                        "description": (
                            "Market to screen: 'france' (Euronext Paris / PEA), "
                            "'america' (Nasdaq + NYSE), crypto, forex, futures, etc."
                        ),
                    },
                    "min_volume": {"type": "number", "description": "Minimum volume filter"},
                    "min_market_cap": {"type": "number"},
                    "min_rsi": {"type": "number", "minimum": 0, "maximum": 100},
                    "max_rsi": {"type": "number", "minimum": 0, "maximum": 100},
                    "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                },
            },
        ),
    ]


# ─────────────────────────────────────────────
# Tool dispatcher
# ─────────────────────────────────────────────


async def handle_tool_call(
    name: str,
    arguments: dict[str, Any],
    client: TradingViewClient,
) -> list[TextContent]:
    """Route a tool call to the appropriate handler."""
    try:
        match name:
            case "tv_get_historical_data":
                hist_inp = GetHistoricalDataInput(**arguments)
                hist_result = await client.get_historical_data(
                    symbol=hist_inp.symbol,
                    exchange=hist_inp.exchange,
                    interval=hist_inp.interval,
                    n_bars=hist_inp.n_bars,
                )
                # Return summary + truncated bars to avoid overwhelming context
                summary = {
                    "symbol": hist_result.symbol,
                    "exchange": hist_result.exchange,
                    "interval": hist_result.interval,
                    "count": hist_result.count,
                    "first_bar": hist_result.bars[0].model_dump() if hist_result.bars else None,
                    "last_bar": hist_result.bars[-1].model_dump() if hist_result.bars else None,
                    "bars": [b.model_dump() for b in hist_result.bars[-50:]],  # last 50 bars
                }
                return [TextContent(type="text", text=json.dumps(summary, indent=2))]

            case "tv_get_quote":
                quote_inp = GetQuoteInput(**arguments)
                quote_result = await client.get_quote(
                    symbol=quote_inp.symbol,
                    exchange=quote_inp.exchange,
                    interval=quote_inp.interval,
                )
                return [TextContent(type="text", text=quote_result.model_dump_json(indent=2))]

            case "tv_get_indicators":
                indic_inp = GetIndicatorsInput(**arguments)
                indic_result = await client.get_indicators(
                    symbol=indic_inp.symbol,
                    exchange=indic_inp.exchange,
                    interval=indic_inp.interval,
                    n_bars=indic_inp.n_bars,
                    indicators=indic_inp.indicators,
                    rsi_length=indic_inp.rsi_length,
                    ema_periods=indic_inp.ema_periods,
                    sma_periods=indic_inp.sma_periods,
                    macd_fast=indic_inp.macd_fast,
                    macd_slow=indic_inp.macd_slow,
                    macd_signal=indic_inp.macd_signal,
                    bb_length=indic_inp.bb_length,
                    bb_std=indic_inp.bb_std,
                    atr_length=indic_inp.atr_length,
                    atr_stop_mult=indic_inp.atr_stop_mult,
                    divergence_lookback=indic_inp.divergence_lookback,
                    benchmark_symbol=indic_inp.benchmark_symbol,
                    benchmark_exchange=indic_inp.benchmark_exchange,
                    include_weekly=indic_inp.include_weekly,
                    include_score=indic_inp.include_score,
                )
                return [TextContent(type="text", text=indic_result.model_dump_json(indent=2))]

            case "tv_screener":
                screener_inp = ScreenerInput(**arguments)
                screener_result = await client.get_screener(screener_inp)
                return [TextContent(type="text", text=screener_result.model_dump_json(indent=2))]

            case _:
                return [TextContent(type="text", text=json.dumps(f"Unknown tool: {name}"))]

    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool %s failed: %s", name, exc)
        error_payload = {"error": str(exc), "tool": name, "arguments": arguments}
        return [TextContent(type="text", text=json.dumps(error_payload, indent=2))]
