"""TradingView data client with TTL cache."""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from collections.abc import Callable
from contextlib import redirect_stdout
from functools import wraps
from typing import Any, TypeVar, cast

import pandas as pd
import pandas_ta_classic as ta  # fork communautaire Python 3.11
from tvDatafeed import Interval as TvInterval
from tvDatafeed import TvDatafeed

from mcp_tradingview.config import settings
from mcp_tradingview.models import (
    HistoricalDataResult,
    IndicatorType,
    IndicatorValues,
    Interval,
    OHLCVBar,
    QuoteResult,
    ScreenerInput,
    ScreenerItem,
    ScreenerResult,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ─────────────────────────────────────────────
# Interval mapping
# ─────────────────────────────────────────────

INTERVAL_MAP: dict[Interval, TvInterval] = {
    Interval.IN_1_MINUTE: TvInterval.in_1_minute,
    Interval.IN_3_MINUTE: TvInterval.in_3_minute,
    Interval.IN_5_MINUTE: TvInterval.in_5_minute,
    Interval.IN_15_MINUTE: TvInterval.in_15_minute,
    Interval.IN_30_MINUTE: TvInterval.in_30_minute,
    Interval.IN_45_MINUTE: TvInterval.in_45_minute,
    Interval.IN_1_HOUR: TvInterval.in_1_hour,
    Interval.IN_2_HOUR: TvInterval.in_2_hour,
    Interval.IN_3_HOUR: TvInterval.in_3_hour,
    Interval.IN_4_HOUR: TvInterval.in_4_hour,
    Interval.IN_DAILY: TvInterval.in_daily,
    Interval.IN_WEEKLY: TvInterval.in_weekly,
    Interval.IN_MONTHLY: TvInterval.in_monthly,
}


# ─────────────────────────────────────────────
# Curated screener universe
# ─────────────────────────────────────────────
#
# tvdatafeed exposes no real screener API, so we screen a curated universe per
# market. Each entry is ``(symbol, exchange)`` using TradingView codes.
#  - ``france``  → Euronext Paris (CAC 40 leaders, all PEA-eligible)
#  - ``america`` → Nasdaq + NYSE blue chips (Nasdaq-100 heavy)

MARKET_SYMBOLS: dict[str, list[tuple[str, str]]] = {
    "france": [
        # CAC 40 leaders
        ("AI", "EURONEXT"), ("OR", "EURONEXT"), ("MC", "EURONEXT"),
        ("RMS", "EURONEXT"), ("SAN", "EURONEXT"), ("TTE", "EURONEXT"),
        ("SU", "EURONEXT"), ("AIR", "EURONEXT"), ("DG", "EURONEXT"),
        ("BNP", "EURONEXT"), ("CS", "EURONEXT"), ("SAF", "EURONEXT"),
        ("EL", "EURONEXT"), ("KER", "EURONEXT"), ("DSY", "EURONEXT"),
        ("STLAP", "EURONEXT"), ("ENGI", "EURONEXT"), ("CAP", "EURONEXT"),
        ("RI", "EURONEXT"), ("GLE", "EURONEXT"),
        # Tech / midcap PEA
        ("STMPA", "EURONEXT"),  # STMicroelectronics
        ("ASML", "EURONEXT"),   # ASML Holding (Euronext Amsterdam)
        ("OVH", "EURONEXT"),    # OVHcloud
        ("ALMDT", "EURONEXT"),  # Median Technologies (Euronext Growth)
        # ETFs PEA Euronext Paris
        ("CW8", "EURONEXT"),    # Amundi MSCI World
        ("PE500", "EURONEXT"),  # Amundi PEA S&P 500 Screened
        ("PUST", "EURONEXT"),   # Amundi PEA Nasdaq-100
        ("PINR", "EURONEXT"),   # Amundi PEA MSCI India
        ("PAEEM", "EURONEXT"),  # Amundi MSCI Emerging Markets
    ],
    "america": [
        ("AAPL", "NASDAQ"), ("MSFT", "NASDAQ"), ("NVDA", "NASDAQ"),
        ("GOOGL", "NASDAQ"), ("META", "NASDAQ"), ("AMZN", "NASDAQ"),
        ("TSLA", "NASDAQ"), ("AVGO", "NASDAQ"), ("COST", "NASDAQ"),
        ("AMD", "NASDAQ"), ("NFLX", "NASDAQ"), ("ADBE", "NASDAQ"),
        ("PEP", "NASDAQ"), ("CSCO", "NASDAQ"), ("QCOM", "NASDAQ"),
        ("TXN", "NASDAQ"), ("JPM", "NYSE"), ("BRK.B", "NYSE"),
        ("V", "NYSE"), ("XOM", "NYSE"),
    ],
    "crypto": [
        ("BTCUSDT", "BINANCE"), ("ETHUSDT", "BINANCE"), ("BNBUSDT", "BINANCE"),
        ("SOLUSDT", "BINANCE"), ("XRPUSDT", "BINANCE"), ("ADAUSDT", "BINANCE"),
        ("DOGEUSDT", "BINANCE"), ("AVAXUSDT", "BINANCE"),
    ],
    "forex": [
        ("EURUSD", "FX"), ("GBPUSD", "FX"), ("USDJPY", "FX"),
        ("USDCHF", "FX"), ("AUDUSD", "FX"), ("USDCAD", "FX"),
    ],
}


# ─────────────────────────────────────────────
# Simple in-memory TTL cache
# ─────────────────────────────────────────────


class TTLCache:
    """Thread-safe in-memory TTL cache."""

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.monotonic() - ts > self._ttl:
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic(), value)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()


def cached(key_fn: Callable[..., str]) -> Callable[[F], F]:
    """Decorator for async methods using the instance's _cache."""

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(self: TradingViewClient, *args: Any, **kwargs: Any) -> Any:
            key = key_fn(*args, **kwargs)
            cached_val = await self._cache.get(key)
            if cached_val is not None:
                logger.debug("Cache hit: %s", key)
                return cached_val
            result = await func(self, *args, **kwargs)
            await self._cache.set(key, result)
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


# ─────────────────────────────────────────────
# Main client
# ─────────────────────────────────────────────


class TradingViewClient:
    """Async-friendly wrapper around tvDatafeed."""

    def __init__(self) -> None:
        self._tv: TvDatafeed | None = None
        self._cache = TTLCache(ttl=settings.cache_ttl_seconds)
        self._connect_lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────

    async def connect(self) -> None:
        """Initialise the tvDatafeed connection (idempotent & concurrency-safe).

        Crucially, this is **not** called before the MCP stdio handshake: the
        connection is established lazily on first use so the server registers
        instantly in Claude Desktop instead of timing out while ``TvDatafeed``
        performs its blocking, network-bound construction.
        """
        if self._tv is not None:
            return
        async with self._connect_lock:
            if self._tv is not None:  # double-checked under lock
                return
            self._tv = await asyncio.to_thread(self._build_tv_client)

    @staticmethod
    def _build_tv_client() -> TvDatafeed:
        """Construct the blocking ``TvDatafeed`` client inside a worker thread.

        ``tvdatafeed`` may emit progress / auth messages on **stdout**; over the
        MCP stdio transport that would corrupt the JSON-RPC stream. We redirect
        stdout to stderr for the duration of construction. The MCP transport
        keeps its own reference to the original stdout buffer, so it is
        unaffected by this temporary swap.
        """
        username = settings.tv_username or None
        password = settings.tv_password or None
        with redirect_stdout(sys.stderr):
            if username and password:
                logger.info("Connecting to TradingView as %s", username)
                return TvDatafeed(username=username, password=password)
            logger.info("Connecting to TradingView anonymously")
            return TvDatafeed()

    async def disconnect(self) -> None:
        self._tv = None
        await self._cache.clear()

    @property
    def tv(self) -> TvDatafeed:
        if self._tv is None:
            raise RuntimeError("Client not connected — call connect() first")
        return self._tv

    # ── Internal helpers ───────────────────────

    async def _fetch_df(
        self,
        symbol: str,
        exchange: str,
        interval: Interval,
        n_bars: int,
    ) -> pd.DataFrame:
        """Fetch raw OHLCV DataFrame from tvDatafeed (thread-offloaded)."""
        await self.connect()  # lazy, idempotent — first call establishes the session
        tv_interval = INTERVAL_MAP[interval]
        df: pd.DataFrame = await asyncio.to_thread(
            self.tv.get_hist,
            symbol=symbol,
            exchange=exchange,
            interval=tv_interval,
            n_bars=n_bars,
        )
        if df is None or df.empty:
            raise ValueError(f"No data returned for {exchange}:{symbol} @ {interval}")
        df.index = pd.to_datetime(df.index)
        return df

    # ── Public tools ───────────────────────────

    async def get_historical_data(
        self,
        symbol: str,
        exchange: str,
        interval: Interval,
        n_bars: int,
    ) -> HistoricalDataResult:
        """Fetch OHLCV bars and return as HistoricalDataResult."""
        cache_key = f"hist:{exchange}:{symbol}:{interval}:{n_bars}"
        cached_val: HistoricalDataResult | None = await self._cache.get(cache_key)
        if cached_val is not None:
            return cached_val

        df = await self._fetch_df(symbol, exchange, interval, n_bars)

        bars: list[OHLCVBar] = [
            OHLCVBar(
                datetime=cast(pd.Timestamp, idx).isoformat(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for idx, row in df.iterrows()
        ]

        result = HistoricalDataResult(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            bars=bars,
            count=len(bars),
        )
        await self._cache.set(cache_key, result)
        return result

    async def get_quote(
        self,
        symbol: str,
        exchange: str,
        interval: Interval,
    ) -> QuoteResult:
        """Get the latest OHLCV bar as a quote."""
        cache_key = f"quote:{exchange}:{symbol}:{interval}"
        cached_val: QuoteResult | None = await self._cache.get(cache_key)
        if cached_val is not None:
            return cached_val

        df = await self._fetch_df(symbol, exchange, interval, n_bars=2)
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else None

        change_pct: float | None = None
        if prev is not None and float(prev["close"]) != 0:
            change_pct = round(
                (float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100,
                4,
            )

        result = QuoteResult(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            timestamp=df.index[-1].isoformat(),
            open=float(last["open"]),
            high=float(last["high"]),
            low=float(last["low"]),
            close=float(last["close"]),
            volume=float(last["volume"]),
            change_pct=change_pct,
        )
        await self._cache.set(cache_key, result)
        return result

    async def get_indicators(
        self,
        symbol: str,
        exchange: str,
        interval: Interval,
        n_bars: int,
        indicators: list[IndicatorType],
        *,
        rsi_length: int = 14,
        ema_periods: list[int] | None = None,
        sma_periods: list[int] | None = None,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_length: int = 20,
        bb_std: float = 2.0,
        atr_length: int = 14,
        atr_stop_mult: float = 2.0,
        divergence_lookback: int = 20,
        benchmark_symbol: str = "",
        benchmark_exchange: str = "EURONEXT",
        include_weekly: bool = False,
        include_score: bool = False,
    ) -> IndicatorValues:
        """Compute technical indicators and return the latest values."""
        if ema_periods is None:
            ema_periods = [20, 50, 200]
        if sma_periods is None:
            sma_periods = [20, 50, 200]

        cache_key = (
            f"indic:{exchange}:{symbol}:{interval}:{n_bars}:"
            f"{sorted(indicators)}:{rsi_length}:{ema_periods}:{sma_periods}:"
            f"{benchmark_symbol}:{benchmark_exchange}:{include_weekly}:{include_score}"
        )
        cached_val: IndicatorValues | None = await self._cache.get(cache_key)
        if cached_val is not None:
            return cached_val

        df = await self._fetch_df(symbol, exchange, interval, n_bars)
        compute_all = IndicatorType.ALL in indicators

        result_kwargs: dict[str, Any] = {
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "timestamp": df.index[-1].isoformat(),
            "close": float(df["close"].iloc[-1]),
        }

        # Intermediaries reused across blocks
        _rsi_series: pd.Series | None = None
        _atr_val: float | None = None

        # ── RSI ──────────────────────────────
        if compute_all or IndicatorType.RSI in indicators:
            rsi_series = ta.rsi(df["close"], length=rsi_length)
            _rsi_series = rsi_series
            result_kwargs["rsi"] = (
                round(float(rsi_series.iloc[-1]), 4) if rsi_series is not None else None
            )

        # ── MACD ─────────────────────────────
        if compute_all or IndicatorType.MACD in indicators:
            macd_df = ta.macd(
                df["close"],
                fast=macd_fast,
                slow=macd_slow,
                signal=macd_signal,
            )
            if macd_df is not None:
                result_kwargs["macd"] = round(float(macd_df.iloc[-1, 0]), 4)
                result_kwargs["macd_hist"] = round(float(macd_df.iloc[-1, 1]), 4)
                result_kwargs["macd_signal"] = round(float(macd_df.iloc[-1, 2]), 4)

        # ── Bollinger Bands ──────────────────
        if compute_all or IndicatorType.BOLLINGER_BANDS in indicators:
            bb_df = ta.bbands(df["close"], length=bb_length, std=bb_std)
            if bb_df is not None:
                result_kwargs["bb_lower"] = round(float(bb_df.iloc[-1, 0]), 4)
                result_kwargs["bb_middle"] = round(float(bb_df.iloc[-1, 1]), 4)
                result_kwargs["bb_upper"] = round(float(bb_df.iloc[-1, 2]), 4)
                # Bandwidth = (upper - lower) / middle; squeeze = in bottom 20th percentile
                bw_series = (bb_df.iloc[:, 2] - bb_df.iloc[:, 0]) / bb_df.iloc[:, 1]
                current_bw = float(bw_series.iloc[-1])
                result_kwargs["bb_bandwidth"] = round(current_bw, 6)
                bw_clean = bw_series.dropna().iloc[-50:]
                if len(bw_clean) > 10:
                    result_kwargs["bb_squeeze"] = bool(current_bw <= float(bw_clean.quantile(0.2)))

        # ── EMA ──────────────────────────────
        if compute_all or IndicatorType.EMA in indicators:
            ema_values: dict[str, float] = {}
            for period in ema_periods:
                ema_series = ta.ema(df["close"], length=period)
                if ema_series is not None:
                    ema_values[f"EMA_{period}"] = round(float(ema_series.iloc[-1]), 4)
            result_kwargs["ema_values"] = ema_values

        # ── SMA ──────────────────────────────
        if compute_all or IndicatorType.SMA in indicators:
            sma_values: dict[str, float] = {}
            for period in sma_periods:
                sma_series = ta.sma(df["close"], length=period)
                if sma_series is not None:
                    sma_values[f"SMA_{period}"] = round(float(sma_series.iloc[-1]), 4)
            result_kwargs["sma_values"] = sma_values

        # ── ATR ──────────────────────────────
        if compute_all or IndicatorType.ATR in indicators:
            atr_series = ta.atr(df["high"], df["low"], df["close"], length=atr_length)
            if atr_series is not None:
                _atr_val = round(float(atr_series.iloc[-1]), 4)
                result_kwargs["atr"] = _atr_val
                # ATR-based stop levels
                close_val = float(df["close"].iloc[-1])
                result_kwargs["stop_long"] = round(close_val - atr_stop_mult * _atr_val, 4)
                result_kwargs["stop_short"] = round(close_val + atr_stop_mult * _atr_val, 4)

        # ── Stochastic ───────────────────────
        if compute_all or IndicatorType.STOCH in indicators:
            stoch_df = ta.stoch(df["high"], df["low"], df["close"])
            if stoch_df is not None:
                result_kwargs["stoch_k"] = round(float(stoch_df.iloc[-1, 0]), 4)
                result_kwargs["stoch_d"] = round(float(stoch_df.iloc[-1, 1]), 4)

        # ── ADX (+ directional indicators) ───
        if compute_all or IndicatorType.ADX in indicators:
            adx_df = ta.adx(df["high"], df["low"], df["close"])
            if adx_df is not None:
                result_kwargs["adx"] = round(float(adx_df.iloc[-1, 0]), 4)
                if adx_df.shape[1] > 1:
                    result_kwargs["adx_plus_di"] = round(float(adx_df.iloc[-1, 1]), 4)
                if adx_df.shape[1] > 2:
                    result_kwargs["adx_minus_di"] = round(float(adx_df.iloc[-1, 2]), 4)

        # ── OBV + Volume MA20 ─────────────────
        if compute_all or IndicatorType.OBV in indicators:
            price_diff = df["close"].diff()
            direction = price_diff.map(lambda x: 1.0 if x > 0.0 else (-1.0 if x < 0.0 else 0.0))
            obv_series = (df["volume"] * direction).fillna(0.0).cumsum()
            result_kwargs["obv"] = round(float(obv_series.iloc[-1]), 2)
            vol_ma = df["volume"].rolling(window=20).mean()
            if not pd.isna(vol_ma.iloc[-1]):
                vol_ma20 = round(float(vol_ma.iloc[-1]), 2)
                result_kwargs["volume_ma20"] = vol_ma20
                if vol_ma20 > 0:
                    result_kwargs["volume_ratio"] = round(
                        float(df["volume"].iloc[-1]) / vol_ma20, 4
                    )

        # ── RSI divergence detection ──────────
        # Splits the lookback window in two halves and compares price/RSI extremes.
        # Bearish: price higher high + RSI lower high → momentum exhaustion.
        # Bullish: price lower low + RSI higher low → selling pressure fading.
        if _rsi_series is not None:
            rsi_clean = _rsi_series.dropna()
            if len(df) >= divergence_lookback and len(rsi_clean) >= divergence_lookback:
                close_win = df["close"].iloc[-divergence_lookback:]
                rsi_win = rsi_clean.iloc[-divergence_lookback:]
                mid = divergence_lookback // 2
                p1_max = float(close_win.iloc[:mid].max())
                p2_max = float(close_win.iloc[mid:].max())
                r1_max = float(rsi_win.iloc[:mid].max())
                r2_max = float(rsi_win.iloc[mid:].max())
                if p2_max > p1_max * 1.005 and r2_max < r1_max * 0.995:
                    result_kwargs["divergence_bearish"] = True
                p1_min = float(close_win.iloc[:mid].min())
                p2_min = float(close_win.iloc[mid:].min())
                r1_min = float(rsi_win.iloc[:mid].min())
                r2_min = float(rsi_win.iloc[mid:].min())
                if p2_min < p1_min * 0.995 and r2_min > r1_min * 1.005:
                    result_kwargs["divergence_bullish"] = True

        # ── Relative strength vs benchmark ────
        if benchmark_symbol:
            try:
                bench_df = await self._fetch_df(
                    benchmark_symbol, benchmark_exchange, interval, n_bars
                )
                def _perf(src: pd.DataFrame, bars: int) -> float | None:
                    if len(src) < bars + 1:
                        return None
                    last = float(src["close"].iloc[-1])
                    past = float(src["close"].iloc[-(bars + 1)])
                    return (last / past - 1) * 100

                sym_1m = _perf(df, 21)
                sym_3m = _perf(df, 63)
                ben_1m = _perf(bench_df, 21)
                ben_3m = _perf(bench_df, 63)
                if sym_1m is not None and ben_1m is not None:
                    result_kwargs["rs_1m"] = round(sym_1m - ben_1m, 4)
                if sym_3m is not None and ben_3m is not None:
                    result_kwargs["rs_3m"] = round(sym_3m - ben_3m, 4)
            except Exception:  # noqa: BLE001
                logger.warning("RS benchmark fetch failed for %s", benchmark_symbol)

        # ── Weekly context (multi-timeframe) ──
        if include_weekly and interval == Interval.IN_DAILY:
            try:
                weekly_df = await self._fetch_df(symbol, exchange, Interval.IN_WEEKLY, 210)
                w_rsi = ta.rsi(weekly_df["close"], length=rsi_length)
                if w_rsi is not None:
                    result_kwargs["weekly_rsi"] = round(float(w_rsi.iloc[-1]), 4)
                w_adx_df = ta.adx(weekly_df["high"], weekly_df["low"], weekly_df["close"])
                if w_adx_df is not None:
                    result_kwargs["weekly_adx"] = round(float(w_adx_df.iloc[-1, 0]), 4)
                ema_len = min(200, len(weekly_df) - 1)
                w_ema = ta.ema(weekly_df["close"], length=ema_len)
                if w_ema is not None:
                    ema_w = float(w_ema.iloc[-1])
                    close_w = float(weekly_df["close"].iloc[-1])
                    result_kwargs["weekly_ema200_distance_pct"] = round(
                        (close_w - ema_w) / ema_w * 100, 4
                    )
                    result_kwargs["weekly_trend"] = "bullish" if close_w > ema_w else "bearish"
            except Exception:  # noqa: BLE001
                logger.warning("Weekly context fetch failed for %s", symbol)

        # ── Composite technical score [-1, +1] ─
        if include_score:
            # Trend component: position vs EMA200 modulated by ADX strength
            trend_s: float = 0.0
            has_trend = False
            ema_200 = result_kwargs.get("ema_values", {}).get("EMA_200")
            if ema_200 is not None and float(ema_200) > 0:
                direction_ema = 1.0 if result_kwargs["close"] > float(ema_200) else -1.0
                adx_v = result_kwargs.get("adx")
                adx_factor = (
                    1.0 if (adx_v is not None and adx_v > 25)
                    else 0.5 if (adx_v is not None and adx_v > 20)
                    else 0.25
                )
                trend_s = direction_ema * adx_factor
                has_trend = True

            # Momentum component: RSI + Stoch + MACD hist
            mom_parts: list[float] = []
            rsi_v = result_kwargs.get("rsi")
            if rsi_v is not None:
                mom_parts.append((float(rsi_v) - 50.0) / 50.0)
            stoch_v = result_kwargs.get("stoch_k")
            if stoch_v is not None:
                mom_parts.append((float(stoch_v) - 50.0) / 50.0)
            hist_v = result_kwargs.get("macd_hist")
            if hist_v is not None:
                if _atr_val is not None and _atr_val > 0:
                    mom_parts.append(math.tanh(float(hist_v) / _atr_val))
                else:
                    mom_parts.append(1.0 if float(hist_v) > 0 else -1.0)
            mom_s: float = (sum(mom_parts) / len(mom_parts)) if mom_parts else 0.0
            mom_s = max(-1.0, min(1.0, mom_s))

            # Volume component: high volume in direction of last move
            vol_s: float = 0.0
            vol_ratio = result_kwargs.get("volume_ratio")
            if vol_ratio is not None and float(vol_ratio) > 1.3 and len(df) >= 2:
                last_move = float(df["close"].iloc[-1]) - float(df["close"].iloc[-2])
                vol_s = 1.0 if last_move > 0 else -1.0

            # RS component: relative vs benchmark
            rs_s: float = 0.0
            rs_3m_v = result_kwargs.get("rs_3m")
            if rs_3m_v is not None:
                rs_s = math.tanh(float(rs_3m_v) / 20.0)

            # Weighted combination — only count available components
            components: list[tuple[float, float]] = []
            if has_trend:
                components.append((trend_s, 0.30))
            if mom_parts:
                components.append((mom_s, 0.35))
            if vol_ratio is not None:
                components.append((vol_s, 0.15))
            if rs_3m_v is not None:
                components.append((rs_s, 0.20))

            total_w = sum(w for _, w in components)
            if total_w > 0:
                score = sum(s * w for s, w in components) / total_w
                result_kwargs["technical_score"] = round(score, 4)

            if has_trend:
                result_kwargs["trend_score"] = round(trend_s, 4)
            if mom_parts:
                result_kwargs["momentum_score"] = round(mom_s, 4)
            if vol_ratio is not None:
                result_kwargs["volume_score"] = round(vol_s, 4)
            if rs_3m_v is not None:
                result_kwargs["rs_score"] = round(rs_s, 4)

        result = IndicatorValues(**result_kwargs)
        await self._cache.set(cache_key, result)
        return result

    async def get_screener(self, params: ScreenerInput) -> ScreenerResult:
        """
        Return a basic screener result.

        Note: tvdatafeed does not expose a full screener API.
        We use a curated list of major symbols + compute RSI on each.
        For a real screener, consider tradingview-ta or a paid API.
        """
        from tradingview_ta import (
            Interval as TaInterval,
        )
        from tradingview_ta import (
            TA_Handler,
        )

        symbols = MARKET_SYMBOLS.get(params.market, [])
        items: list[ScreenerItem] = []

        ta_interval_map = {
            "1D": TaInterval.INTERVAL_1_DAY,
            "1W": TaInterval.INTERVAL_1_WEEK,
            "4H": TaInterval.INTERVAL_4_HOURS,
            "1H": TaInterval.INTERVAL_1_HOUR,
        }
        ta_interval = ta_interval_map.get("1D", TaInterval.INTERVAL_1_DAY)

        async def _analyze_symbol(symbol: str, exchange: str) -> ScreenerItem | None:
            try:
                handler = TA_Handler(
                    symbol=symbol,
                    exchange=exchange,
                    screener=params.market if params.market != "crypto" else "crypto",
                    interval=ta_interval,
                )
                analysis = await asyncio.to_thread(handler.get_analysis)
                indicators = analysis.indicators
                rsi_val: float | None = indicators.get("RSI")
                close_val: float | None = indicators.get("close")
                volume_val: float | None = indicators.get("volume")

                # Apply filters
                if params.min_rsi is not None and rsi_val is not None and rsi_val < params.min_rsi:
                    return None
                if params.max_rsi is not None and rsi_val is not None and rsi_val > params.max_rsi:
                    return None
                if (
                    params.min_volume is not None
                    and volume_val is not None
                    and volume_val < params.min_volume
                ):
                    return None

                return ScreenerItem(
                    symbol=symbol,
                    close=close_val,
                    volume=volume_val,
                    rsi=round(rsi_val, 2) if rsi_val is not None else None,
                    extra={"recommendation": analysis.summary.get("RECOMMENDATION")},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Screener failed for %s: %s", symbol, exc)
                return None

        tasks = [_analyze_symbol(sym, exch) for sym, exch in symbols]
        raw_results = await asyncio.gather(*tasks)

        items = [r for r in raw_results if r is not None][: params.limit]

        return ScreenerResult(
            market=params.market,
            count=len(items),
            items=items,
        )
