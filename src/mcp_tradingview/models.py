"""Pydantic models for MCP TradingView tool inputs and outputs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────


class Interval(StrEnum):
    """TradingView chart intervals."""

    IN_1_MINUTE = "1"
    IN_3_MINUTE = "3"
    IN_5_MINUTE = "5"
    IN_15_MINUTE = "15"
    IN_30_MINUTE = "30"
    IN_45_MINUTE = "45"
    IN_1_HOUR = "1H"
    IN_2_HOUR = "2H"
    IN_3_HOUR = "3H"
    IN_4_HOUR = "4H"
    IN_DAILY = "1D"
    IN_WEEKLY = "1W"
    IN_MONTHLY = "1M"


class ScreenerMarket(StrEnum):
    """Markets available in the TradingView screener.

    The values match ``tradingview_ta`` screener (country) codes. For a PEA,
    ``FRANCE`` targets Euronext Paris while ``AMERICA`` covers the US venues
    (Nasdaq / NYSE).
    """

    AMERICA = "america"  # Nasdaq + NYSE
    FRANCE = "france"  # Euronext Paris (PEA)
    CRYPTO = "crypto"
    FOREX = "forex"
    FUTURES = "futures"
    INDIA = "india"
    BRAZIL = "brazil"
    AUSTRALIA = "australia"
    CANADA = "canada"
    EUROPE = "europe"
    HONG_KONG = "hongkong"


class IndicatorType(StrEnum):
    RSI = "rsi"
    MACD = "macd"
    BOLLINGER_BANDS = "bbands"
    EMA = "ema"
    SMA = "sma"
    ATR = "atr"
    STOCH = "stoch"
    ADX = "adx"
    OBV = "obv"
    ALL = "all"


# ─────────────────────────────────────────────
# Input schemas
# ─────────────────────────────────────────────


class GetHistoricalDataInput(BaseModel):
    """Input for get_historical_data tool."""

    symbol: str = Field(..., description="Ticker symbol, e.g. 'BTCUSDT', 'AAPL'")
    exchange: str = Field(default="EURONEXT", description="Exchange, e.g. 'BINANCE', 'NASDAQ'")
    interval: Interval = Field(default=Interval.IN_DAILY, description="Chart interval")
    n_bars: int = Field(default=500, ge=10, le=5000, description="Number of bars to fetch")

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("exchange")
    @classmethod
    def uppercase_exchange(cls, v: str) -> str:
        return v.strip().upper()


class GetIndicatorsInput(BaseModel):
    """Input for get_indicators tool."""

    symbol: str = Field(..., description="Ticker symbol")
    exchange: str = Field(default="EURONEXT", description="Exchange")
    interval: Interval = Field(default=Interval.IN_DAILY)
    n_bars: int = Field(default=200, ge=50, le=5000)
    indicators: list[IndicatorType] = Field(
        default=[IndicatorType.ALL],
        description="List of indicators to compute",
    )
    # RSI
    rsi_length: int = Field(default=14, ge=2, le=100)
    # EMA / SMA periods
    ema_periods: list[int] = Field(default=[20, 50, 200])
    sma_periods: list[int] = Field(default=[20, 50, 200])
    # MACD
    macd_fast: int = Field(default=12, ge=2)
    macd_slow: int = Field(default=26, ge=2)
    macd_signal: int = Field(default=9, ge=2)
    # Bollinger Bands
    bb_length: int = Field(default=20, ge=2)
    bb_std: float = Field(default=2.0, ge=0.5, le=5.0)
    # ATR
    atr_length: int = Field(default=14, ge=2)
    # ATR stop levels multiplier
    atr_stop_mult: float = Field(default=2.0, ge=0.5, le=5.0)
    # Divergence detection window (bars)
    divergence_lookback: int = Field(default=20, ge=10, le=50)
    # Relative strength vs benchmark (empty string = skip, extra API call)
    benchmark_symbol: str = Field(default="", description="Benchmark ticker for RS (e.g. 'CW8'). Empty = skip.")
    benchmark_exchange: str = Field(default="EURONEXT")
    # Weekly context (requires extra API call)
    include_weekly: bool = Field(default=False, description="Fetch weekly bars for multi-timeframe context")
    # Composite technical score [-1, +1]
    include_score: bool = Field(default=False, description="Compute composite technical score")

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("exchange")
    @classmethod
    def uppercase_exchange(cls, v: str) -> str:
        return v.strip().upper()


class GetQuoteInput(BaseModel):
    """Input for get_quote tool (latest price)."""

    symbol: str = Field(..., description="Ticker symbol")
    exchange: str = Field(default="EURONEXT", description="Exchange")
    interval: Interval = Field(default=Interval.IN_DAILY)

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.strip().upper()


class ScreenerInput(BaseModel):
    """Input for screener tool."""

    market: ScreenerMarket = Field(default=ScreenerMarket.FRANCE)
    min_volume: float | None = Field(default=None, ge=0)
    min_market_cap: float | None = Field(default=None, ge=0)
    min_rsi: float | None = Field(default=None, ge=0, le=100)
    max_rsi: float | None = Field(default=None, ge=0, le=100)
    limit: int = Field(default=20, ge=1, le=100)


# ─────────────────────────────────────────────
# Output schemas
# ─────────────────────────────────────────────


class OHLCVBar(BaseModel):
    """Single OHLCV bar."""

    datetime: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class HistoricalDataResult(BaseModel):
    """Result for get_historical_data."""

    symbol: str
    exchange: str
    interval: str
    bars: list[OHLCVBar]
    count: int


class IndicatorValues(BaseModel):
    """Computed indicator values (latest bar)."""

    symbol: str
    exchange: str
    interval: str
    timestamp: str
    close: float
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    ema_values: dict[str, float] = Field(default_factory=dict)
    sma_values: dict[str, float] = Field(default_factory=dict)
    atr: float | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None
    adx: float | None = None
    adx_plus_di: float | None = None
    adx_minus_di: float | None = None
    # BB derived
    bb_bandwidth: float | None = None
    bb_squeeze: bool | None = None
    # OBV + volume
    obv: float | None = None
    volume_ma20: float | None = None
    volume_ratio: float | None = None
    # ATR-based stops
    stop_long: float | None = None
    stop_short: float | None = None
    # Divergence flags (RSI vs price)
    divergence_bullish: bool | None = None
    divergence_bearish: bool | None = None
    # Relative strength vs benchmark
    rs_1m: float | None = None
    rs_3m: float | None = None
    # Weekly timeframe context
    weekly_rsi: float | None = None
    weekly_adx: float | None = None
    weekly_trend: str | None = None
    weekly_ema200_distance_pct: float | None = None
    # Composite technical score [-1, +1] and sub-scores
    technical_score: float | None = None
    trend_score: float | None = None
    momentum_score: float | None = None
    volume_score: float | None = None
    rs_score: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class QuoteResult(BaseModel):
    """Latest quote."""

    symbol: str
    exchange: str
    interval: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    change_pct: float | None = None


class ScreenerItem(BaseModel):
    """Single screener result row."""

    symbol: str
    name: str | None = None
    close: float | None = None
    volume: float | None = None
    market_cap: float | None = None
    rsi: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ScreenerResult(BaseModel):
    """Screener results."""

    market: str
    count: int
    items: list[ScreenerItem]
