"""Tests for MCP TradingView server."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from mcp_tradingview.client import TradingViewClient, TTLCache
from mcp_tradingview.models import (
    GetHistoricalDataInput,
    GetIndicatorsInput,
    HistoricalDataResult,
    IndicatorType,
    Interval,
    OHLCVBar,
    QuoteResult,
)
from mcp_tradingview.tools import build_tools, handle_tool_call

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame matching tvdatafeed output."""
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    data = {
        "open":   [40000.0, 41000.0, 42000.0, 41500.0, 43000.0],
        "high":   [41000.0, 42000.0, 43000.0, 42500.0, 44000.0],
        "low":    [39500.0, 40500.0, 41500.0, 41000.0, 42500.0],
        "close":  [40500.0, 41500.0, 42500.0, 42000.0, 43500.0],
        "volume": [1000.0,  1200.0,  1100.0,  900.0,   1300.0],
    }
    return pd.DataFrame(data, index=dates)


@pytest.fixture
def mock_client(sample_df: pd.DataFrame) -> TradingViewClient:
    """TradingViewClient with mocked tvDatafeed."""
    client = TradingViewClient()
    mock_tv = MagicMock()
    mock_tv.get_hist.return_value = sample_df
    client._tv = mock_tv
    return client


# ─────────────────────────────────────────────
# TTLCache tests
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ttl_cache_set_get() -> None:
    cache = TTLCache(ttl=60)
    await cache.set("key1", {"data": 42})
    result = await cache.get("key1")
    assert result == {"data": 42}


@pytest.mark.asyncio
async def test_ttl_cache_miss() -> None:
    cache = TTLCache(ttl=60)
    result = await cache.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_ttl_cache_expiry() -> None:
    """Test that expired entries are evicted."""
    import time
    cache = TTLCache(ttl=0)  # TTL=0 → always expired
    await cache.set("key", "value")
    # Force expiry by manipulating stored timestamp
    async with cache._lock:
        cache._store["key"] = (time.monotonic() - 1, "value")
    result = await cache.get("key")
    assert result is None


@pytest.mark.asyncio
async def test_ttl_cache_clear() -> None:
    cache = TTLCache(ttl=60)
    await cache.set("k1", 1)
    await cache.set("k2", 2)
    await cache.clear()
    assert await cache.get("k1") is None
    assert await cache.get("k2") is None


# ─────────────────────────────────────────────
# Input model tests
# ─────────────────────────────────────────────


def test_get_historical_data_input_uppercase() -> None:
    inp = GetHistoricalDataInput(symbol="btcusdt", exchange="binance")
    assert inp.symbol == "BTCUSDT"
    assert inp.exchange == "BINANCE"


def test_get_historical_data_input_defaults() -> None:
    inp = GetHistoricalDataInput(symbol="AAPL")
    assert inp.interval == Interval.IN_DAILY
    assert inp.n_bars == 500


def test_get_indicators_input_validation() -> None:
    inp = GetIndicatorsInput(symbol="eth", exchange="binance", n_bars=100)
    assert inp.symbol == "ETH"
    assert inp.indicators == [IndicatorType.ALL]


# ─────────────────────────────────────────────
# Client tests
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_historical_data(mock_client: TradingViewClient, sample_df: pd.DataFrame) -> None:
    result = await mock_client.get_historical_data(
        symbol="BTCUSDT",
        exchange="BINANCE",
        interval=Interval.IN_DAILY,
        n_bars=5,
    )
    assert isinstance(result, HistoricalDataResult)
    assert result.symbol == "BTCUSDT"
    assert result.count == 5
    assert len(result.bars) == 5
    first_bar = result.bars[0]
    assert isinstance(first_bar, OHLCVBar)
    assert first_bar.close == 40500.0


@pytest.mark.asyncio
async def test_get_quote(mock_client: TradingViewClient) -> None:
    result = await mock_client.get_quote(
        symbol="BTCUSDT",
        exchange="BINANCE",
        interval=Interval.IN_DAILY,
    )
    assert isinstance(result, QuoteResult)
    assert result.close == 43500.0
    assert result.change_pct is not None
    # change = (43500 - 42000) / 42000 * 100
    expected_change = round((43500 - 42000) / 42000 * 100, 4)
    assert result.change_pct == pytest.approx(expected_change)


@pytest.mark.asyncio
async def test_get_historical_data_caches(mock_client: TradingViewClient) -> None:
    """Second call should use cache, not call tvdatafeed again."""
    await mock_client.get_historical_data("BTCUSDT", "BINANCE", Interval.IN_DAILY, 5)
    await mock_client.get_historical_data("BTCUSDT", "BINANCE", Interval.IN_DAILY, 5)
    # tvdatafeed.get_hist should have been called only once
    assert mock_client._tv.get_hist.call_count == 1  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_client_not_connected_raises() -> None:
    client = TradingViewClient()
    with pytest.raises(RuntimeError, match="not connected"):
        _ = client.tv


# ─────────────────────────────────────────────
# Tool tests
# ─────────────────────────────────────────────


def test_build_tools_count() -> None:
    tools = build_tools()
    names = [t.name for t in tools]
    assert "tv_get_historical_data" in names
    assert "tv_get_quote" in names
    assert "tv_get_indicators" in names
    assert "tv_screener" in names


def test_build_tools_schemas() -> None:
    tools = build_tools()
    for tool in tools:
        assert "properties" in tool.inputSchema
        assert tool.description


@pytest.mark.asyncio
async def test_handle_tool_get_quote(mock_client: TradingViewClient) -> None:
    result = await handle_tool_call(
        "tv_get_quote",
        {"symbol": "BTCUSDT", "exchange": "BINANCE", "interval": "1D"},
        mock_client,
    )
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data["symbol"] == "BTCUSDT"
    assert data["close"] == 43500.0


@pytest.mark.asyncio
async def test_handle_tool_get_historical_data(mock_client: TradingViewClient) -> None:
    result = await handle_tool_call(
        "tv_get_historical_data",
        {"symbol": "AAPL", "exchange": "NASDAQ", "interval": "1D", "n_bars": 10},
        mock_client,
    )
    data = json.loads(result[0].text)
    assert data["symbol"] == "AAPL"
    assert data["count"] == 5


@pytest.mark.asyncio
async def test_handle_tool_unknown(mock_client: TradingViewClient) -> None:
    result = await handle_tool_call("nonexistent_tool", {}, mock_client)
    data = json.loads(result[0].text)
    assert "Unknown tool" in data


@pytest.mark.asyncio
async def test_handle_tool_error_returns_json(mock_client: TradingViewClient) -> None:
    """Invalid arguments should return a JSON error, not raise."""
    result = await handle_tool_call(
        "tv_get_quote",
        {"symbol": ""},  # empty symbol will fail validation
        mock_client,
    )
    # Should return without crashing
    assert len(result) == 1
