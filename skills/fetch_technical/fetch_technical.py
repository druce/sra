#!/usr/bin/env python3
"""
Technical Analysis Skill — Weekly chart + daily indicator computation.

Produces two artifacts:
artifacts/chart.png                 - Weekly candlestick with MA13/MA52, volume,
                                    relative strength vs SPX.
artifacts/technical_analysis.json   - RSI, MACD, ATR, Bollinger, SMAs,
                                    trend signals, and narrative analysis.

Usage:
    ./skills/fetch_technical.py SYMBOL --workdir DIR

Exit codes:
    0  Both artifacts produced
    1  Partial — one artifact produced
    2  Nothing produced

Only the final JSON manifest goes to stdout.
All progress / diagnostic output goes to stderr.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import talib
import yfinance as yf
from plotly.subplots import make_subplots

# Add skills directory to path for local imports
_SKILLS_DIR = Path(__file__).resolve().parent.parent
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

from utils import setup_logging, validate_symbol, ensure_directory, default_workdir  # noqa: E402


from config import (  # noqa: E402
    SMA_SHORT_PERIOD,
    SMA_MEDIUM_PERIOD,
    SMA_LONG_PERIOD,
    MA_WEEKLY_SHORT,
    MA_WEEKLY_LONG,
    RSI_PERIOD,
    MACD_FAST_PERIOD,
    MACD_SLOW_PERIOD,
    MACD_SIGNAL_PERIOD,
    ATR_PERIOD,
    BOLLINGER_PERIOD,
    BOLLINGER_STD_DEV,
    CHART_HISTORY_YEARS,
    CHART_HISTORY_DAYS,
    CHART_WIDTH,
    CHART_HEIGHT,
    CHART_SCALE,
    VOLUME_AVERAGE_DAYS,
)

logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten_yf_column(df: pd.DataFrame, col: str) -> np.ndarray:
    """Extract a column from a (possibly multi-index) yfinance DataFrame
    and return a flat float64 numpy array."""
    series = df[col]
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    return np.array(series.values, dtype=np.float64).flatten()


# ---------------------------------------------------------------------------
# save_chart
# ---------------------------------------------------------------------------

def save_chart(symbol: str, work_dir: Path) -> bool:
    """Generate a weekly candlestick chart with MA13/MA52, volume, and
    relative strength vs SPX.  Writes ``artifacts/chart.png``.

    Returns True on success, False otherwise.
    """
    logger.info(f"[chart] Downloading weekly data for {symbol} "
                f"({CHART_HISTORY_YEARS}yr)...")

    try:
        symbol_df = yf.download(
            symbol,
            interval="1wk",
            period=f"{CHART_HISTORY_YEARS}y",
            progress=False,
        )
        spx_df = yf.download(
            "^GSPC",
            interval="1wk",
            period=f"{CHART_HISTORY_YEARS}y",
            progress=False,
        )

        if symbol_df.empty:
            logger.error(f"[chart] No data returned for {symbol}")
            return False
        if spx_df.empty:
            logger.error("[chart] No data returned for ^GSPC")
            return False

        # Flatten multi-index columns produced by newer yfinance versions
        if isinstance(symbol_df.columns, pd.MultiIndex):
            symbol_df.columns = symbol_df.columns.get_level_values(0)
        if isinstance(spx_df.columns, pd.MultiIndex):
            spx_df.columns = spx_df.columns.get_level_values(0)

        # Compute weekly moving averages
        symbol_df[f"MA{MA_WEEKLY_SHORT}"] = symbol_df["Close"].rolling(
            window=MA_WEEKLY_SHORT
        ).mean()
        symbol_df[f"MA{MA_WEEKLY_LONG}"] = symbol_df["Close"].rolling(
            window=MA_WEEKLY_LONG
        ).mean()

        # Compute relative strength vs SPX
        # Align dates, compute ratio, normalise to first available value
        common_idx = symbol_df.index.intersection(spx_df.index)
        if len(common_idx) > 1:
            sym_close = symbol_df.loc[common_idx, "Close"]
            spx_close = spx_df.loc[common_idx, "Close"]
            rs = sym_close / spx_close
            rs = rs / rs.iloc[0]  # normalise
            symbol_df.loc[common_idx, "RS"] = rs
        else:
            symbol_df["RS"] = np.nan

        # ----- Build chart -----
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.75, 0.25],
            vertical_spacing=0.03,
            subplot_titles=(
                f"{symbol} — Weekly",
                "Volume & Relative Strength vs SPX",
            ),
        )

        # Candlestick
        fig.add_trace(
            go.Candlestick(
                x=symbol_df.index,
                open=symbol_df["Open"],
                high=symbol_df["High"],
                low=symbol_df["Low"],
                close=symbol_df["Close"],
                name=symbol,
                increasing_line_color="#26a69a",
                decreasing_line_color="#ef5350",
            ),
            row=1,
            col=1,
        )

        # MA lines
        fig.add_trace(
            go.Scatter(
                x=symbol_df.index,
                y=symbol_df[f"MA{MA_WEEKLY_SHORT}"],
                line=dict(color="blue", width=1),
                name=f"MA{MA_WEEKLY_SHORT}",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=symbol_df.index,
                y=symbol_df[f"MA{MA_WEEKLY_LONG}"],
                line=dict(color="red", width=1),
                name=f"MA{MA_WEEKLY_LONG}",
            ),
            row=1,
            col=1,
        )

        # Volume bar
        colors = [
            "#26a69a" if c >= o else "#ef5350"
            for c, o in zip(symbol_df["Close"], symbol_df["Open"])
        ]
        fig.add_trace(
            go.Bar(
                x=symbol_df.index,
                y=symbol_df["Volume"],
                marker_color=colors,
                name="Volume",
                opacity=0.5,
            ),
            row=2,
            col=1,
        )

        # Relative strength overlay on volume subplot (secondary y-axis)
        if "RS" in symbol_df.columns and symbol_df["RS"].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=symbol_df.index,
                    y=symbol_df["RS"],
                    line=dict(color="orange", width=1.5),
                    name="RS vs SPX",
                    yaxis="y4",
                ),
                row=2,
                col=1,
            )

        # Layout
        fig.update_layout(
            width=CHART_WIDTH,
            height=CHART_HEIGHT,
            xaxis_rangeslider_visible=False,
            template="plotly_white",
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                font=dict(size=9),
            ),
            margin=dict(l=50, r=50, t=60, b=30),
        )

        # Write image
        artifacts_dir = ensure_directory(work_dir / "artifacts")
        chart_path = artifacts_dir / "chart.png"
        fig.write_image(str(chart_path), scale=CHART_SCALE)

        logger.info(f"[chart] Saved {chart_path}")
        return True

    except Exception as exc:
        logger.error(f"[chart] {exc}", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# save_technical_analysis
# ---------------------------------------------------------------------------

def save_technical_analysis(symbol: str, work_dir: Path) -> dict:
    """Compute daily technical indicators using TA-Lib and write
    ``artifacts/technical_analysis.json``.

    Returns a dict with a ``summary`` key on success, or an empty dict on
    failure.  The full analysis payload is always written to disk when
    computation succeeds.
    """
    logger.info(f"[technical] Downloading daily data for {symbol} "
                f"({CHART_HISTORY_DAYS}d)...")

    try:
        df = yf.download(
            symbol,
            period=f"{CHART_HISTORY_DAYS}d",
            progress=False,
        )

        if df.empty:
            logger.error(f"[technical] No daily data for {symbol}")
            return {}

        # Flatten multi-index columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close = _flatten_yf_column(df, "Close")
        high = _flatten_yf_column(df, "High")
        low = _flatten_yf_column(df, "Low")
        volume = _flatten_yf_column(df, "Volume")

        latest_close = float(close[-1])
        latest_date = str(df.index[-1].date())

        # ----- Moving averages -----
        sma_20 = talib.SMA(close, timeperiod=SMA_SHORT_PERIOD)
        sma_50 = talib.SMA(close, timeperiod=SMA_MEDIUM_PERIOD)
        sma_200 = talib.SMA(close, timeperiod=SMA_LONG_PERIOD)

        # ----- RSI -----
        rsi = talib.RSI(close, timeperiod=RSI_PERIOD)

        # ----- MACD -----
        macd_line, macd_signal, macd_hist = talib.MACD(
            close,
            fastperiod=MACD_FAST_PERIOD,
            slowperiod=MACD_SLOW_PERIOD,
            signalperiod=MACD_SIGNAL_PERIOD,
        )

        # ----- ATR -----
        atr = talib.ATR(high, low, close, timeperiod=ATR_PERIOD)

        # ----- Bollinger Bands -----
        bb_upper, bb_middle, bb_lower = talib.BBANDS(
            close,
            timeperiod=BOLLINGER_PERIOD,
            nbdevup=BOLLINGER_STD_DEV,
            nbdevdn=BOLLINGER_STD_DEV,
            matype=0,
        )

        # ----- Volume average -----
        vol_avg = float(np.nanmean(volume[-VOLUME_AVERAGE_DAYS:]))
        vol_latest = float(volume[-1])

        # ----- Helpers to safely get the last non-NaN value -----
        def _last(arr):
            val = arr[-1]
            return None if np.isnan(val) else round(float(val), 2)

        sma20_val = _last(sma_20)
        sma50_val = _last(sma_50)
        sma200_val = _last(sma_200)
        rsi_val = _last(rsi)
        macd_val = _last(macd_line)
        macd_sig_val = _last(macd_signal)
        macd_hist_val = _last(macd_hist)
        atr_val = _last(atr)
        bb_upper_val = _last(bb_upper)
        bb_middle_val = _last(bb_middle)
        bb_lower_val = _last(bb_lower)

        # ----- Trend signals -----
        trend_signals = {}

        # SMA trend
        if sma50_val is not None and sma200_val is not None:
            if sma50_val > sma200_val:
                trend_signals["golden_cross"] = True
                trend_signals["death_cross"] = False
            else:
                trend_signals["golden_cross"] = False
                trend_signals["death_cross"] = True

        # Price vs SMAs
        if sma20_val is not None:
            trend_signals["above_sma20"] = latest_close > sma20_val
        if sma50_val is not None:
            trend_signals["above_sma50"] = latest_close > sma50_val
        if sma200_val is not None:
            trend_signals["above_sma200"] = latest_close > sma200_val

        # RSI zones
        if rsi_val is not None:
            trend_signals["rsi_overbought"] = rsi_val > 70
            trend_signals["rsi_oversold"] = rsi_val < 30

        # MACD signal
        if macd_hist_val is not None:
            trend_signals["macd_bullish"] = macd_hist_val > 0
            trend_signals["macd_bearish"] = macd_hist_val < 0

        # Bollinger position
        if bb_upper_val is not None and bb_lower_val is not None:
            trend_signals["above_upper_bb"] = latest_close > bb_upper_val
            trend_signals["below_lower_bb"] = latest_close < bb_lower_val

        # Volume signal
        if vol_avg > 0:
            trend_signals["volume_above_avg"] = vol_latest > vol_avg

        # ----- Narrative analysis -----
        analysis_parts = []

        # Price level
        analysis_parts.append(
            f"{symbol} closed at ${latest_close:.2f} on {latest_date}."
        )

        # SMA analysis
        sma_comments = []
        if sma200_val is not None:
            rel = "above" if latest_close > sma200_val else "below"
            pct = ((latest_close - sma200_val) / sma200_val) * 100
            sma_comments.append(
                f"Price is {rel} the 200-day SMA (${sma200_val:.2f}), "
                f"{pct:+.1f}% from it."
            )
        if sma50_val is not None:
            rel = "above" if latest_close > sma50_val else "below"
            sma_comments.append(
                f"Price is {rel} the 50-day SMA (${sma50_val:.2f})."
            )
        if sma_comments:
            analysis_parts.append(" ".join(sma_comments))

        # RSI
        if rsi_val is not None:
            if rsi_val > 70:
                rsi_comment = f"RSI({RSI_PERIOD}) is {rsi_val:.1f} (overbought territory)."
            elif rsi_val < 30:
                rsi_comment = f"RSI({RSI_PERIOD}) is {rsi_val:.1f} (oversold territory)."
            else:
                rsi_comment = f"RSI({RSI_PERIOD}) is {rsi_val:.1f} (neutral)."
            analysis_parts.append(rsi_comment)

        # MACD
        if macd_hist_val is not None:
            direction = "bullish" if macd_hist_val > 0 else "bearish"
            analysis_parts.append(
                f"MACD histogram is {direction} at {macd_hist_val:.2f}."
            )

        # ATR
        if atr_val is not None:
            analysis_parts.append(
                f"ATR({ATR_PERIOD}) is ${atr_val:.2f}, indicating "
                f"{'high' if atr_val > latest_close * 0.03 else 'moderate'} "
                f"volatility."
            )

        # Bollinger
        if bb_upper_val is not None and bb_lower_val is not None:
            if latest_close > bb_upper_val:
                bb_comment = "Price is above the upper Bollinger Band (potential overbought)."
            elif latest_close < bb_lower_val:
                bb_comment = "Price is below the lower Bollinger Band (potential oversold)."
            else:
                bb_pct = (
                    (latest_close - bb_lower_val)
                    / (bb_upper_val - bb_lower_val)
                    * 100
                )
                bb_comment = (
                    f"Price is at {bb_pct:.0f}% of the Bollinger Band range "
                    f"(${bb_lower_val:.2f} - ${bb_upper_val:.2f})."
                )
            analysis_parts.append(bb_comment)

        analysis_text = "\n".join(analysis_parts)

        # ----- Assemble payload -----
        payload = {
            "symbol": symbol,
            "date": latest_date,
            "close": latest_close,
            "indicators": {
                "sma_20": sma20_val,
                "sma_50": sma50_val,
                "sma_200": sma200_val,
                "rsi": rsi_val,
                "macd": macd_val,
                "macd_signal": macd_sig_val,
                "macd_histogram": macd_hist_val,
                "atr": atr_val,
                "bollinger_upper": bb_upper_val,
                "bollinger_middle": bb_middle_val,
                "bollinger_lower": bb_lower_val,
                "volume_latest": vol_latest,
                "volume_avg_20d": round(vol_avg, 0),
            },
            "trend_signals": trend_signals,
            "analysis": analysis_text,
        }

        # Write JSON
        artifacts_dir = ensure_directory(work_dir / "artifacts")
        out_path = artifacts_dir / "technical_analysis.json"
        with out_path.open("w") as f:
            json.dump(payload, f, indent=2, default=str)

        logger.info(f"[technical] Saved {out_path}")

        # Build compact summary string for the manifest
        parts = []
        if rsi_val is not None:
            parts.append(f"RSI {rsi_val:.1f}")
        if macd_hist_val is not None:
            parts.append("MACD bullish" if macd_hist_val >
                         0 else "MACD bearish")
        if sma200_val is not None:
            rel = "above" if latest_close > sma200_val else "below"
            parts.append(f"{rel} 200SMA")
        if atr_val is not None:
            parts.append(f"ATR ${atr_val:.2f}")

        return {"summary": ", ".join(parts) if parts else "indicators computed"}

    except Exception as exc:
        logger.error(f"[technical] {exc}", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Technical analysis skill: weekly chart + daily indicators",
    )
    parser.add_argument("symbol", help="Stock ticker symbol")
    parser.add_argument(
        "--workdir",
        default=None,
        help="Working directory (default: work/SYMBOL_YYYYMMDD)",
    )

    args = parser.parse_args()

    try:
        symbol = validate_symbol(args.symbol)
    except ValueError as exc:
        logger.error(f"{exc}")
        manifest = {
            "status": "error",
            "artifacts": [],
            "error": str(exc),
        }
        print(json.dumps(manifest, indent=2))
        return 2

    work_dir = Path(args.workdir or default_workdir(symbol))
    logger.info(f"=== research_technical: {symbol}  workdir={work_dir} ===")

    # ---- Task 1: chart ----
    chart_ok = save_chart(symbol, work_dir)

    # ---- Task 2: technical analysis ----
    ta_result = save_technical_analysis(symbol, work_dir)
    ta_ok = bool(ta_result)

    # ---- Build manifest ----
    artifacts = []

    if chart_ok:
        artifacts.append({
            "name": "chart",
            "path": "artifacts/chart.png",
            "format": "png",
            "source": "yfinance+plotly",
            "summary": (
                f"{CHART_HISTORY_YEARS}yr weekly candlestick, "
                f"MA{MA_WEEKLY_SHORT}/MA{MA_WEEKLY_LONG}, "
                f"relative strength vs SPX"
            ),
        })

    if ta_ok:
        artifacts.append({
            "name": "technical_analysis",
            "path": "artifacts/technical_analysis.json",
            "format": "json",
            "source": "yfinance+talib",
            "summary": ta_result.get("summary", "indicators computed"),
        })

    succeeded = sum([chart_ok, ta_ok])

    if succeeded == 2:
        status = "complete"
        error = None
        exit_code = 0
    elif succeeded == 1:
        status = "partial"
        failed_names = []
        if not chart_ok:
            failed_names.append("chart")
        if not ta_ok:
            failed_names.append("technical_analysis")
        error = f"Failed: {', '.join(failed_names)}"
        exit_code = 1
    else:
        status = "error"
        error = "Both chart and technical_analysis failed"
        exit_code = 2

    manifest = {
        "status": status,
        "artifacts": artifacts,
        "error": error,
    }

    # Only the manifest goes to stdout
    print(json.dumps(manifest, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
