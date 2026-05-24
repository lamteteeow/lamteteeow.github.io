import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
from dotenv import load_dotenv
from fredapi import Fred
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

load_dotenv()
FRED_API_KEY = os.getenv("FRED_API_KEY")

if not FRED_API_KEY:
    print("Warning: FRED_API_KEY not found. Using dummy data if API fails.")


def monthly_downsample(records):
    """Resample a list of {date, value} dicts to end-of-month values."""
    if not records:
        return []
    df = pl.DataFrame(records)
    df = df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
    df = df.with_columns(pl.col("date").dt.truncate("1mo").alias("month_start"))
    df = df.sort("date").group_by("month_start").last().sort("month_start")
    df = df.with_columns(pl.col("date").dt.strftime("%Y-%m-%d"))
    return df.select(["date", "value"]).to_dicts()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((OSError, ValueError, Exception)),
)
def get_fred_data_retry(fred, series_id, start_date="1970-01-01"):
    data = fred.get_series(series_id, observation_start=start_date)
    import math

    dates = [d.strftime("%Y-%m-%d") for d in data.index]
    values = [float(v) if not math.isnan(v) else None for v in data.values]
    df = pl.DataFrame({"date": dates, "value": values})
    df = df.drop_nulls()
    df = df.filter(~pl.col("value").is_nan())
    return df.to_dicts()


def sub_score_linear(value, low, high, direction="up"):
    """Piecewise-linear sub-score in [0, 1]. direction='up': higher raw value = higher risk."""
    if direction == "up":
        if value <= low:
            return 0.0
        if value >= high:
            return 1.0
        return (value - low) / (high - low)
    else:
        if value >= low:
            return 0.0
        if value <= high:
            return 1.0
        return (low - value) / (low - high)


def z_score_sub_score(value, series, direction="up"):
    """Z-score based risk sub-score in [0,1].
    Computes z = (value - mean) / std over full history,
    maps to [0,1] via clamp(z/2, 0, 1) for 'up', clamp(-z/2, 0, 1) for 'down'."""
    if value is None or not series or len(series) < 4:
        return 0.0
    vals = pl.Series([float(v["value"]) for v in series])
    mean = float(vals.mean())
    std = float(vals.std())
    if std == 0:
        return 0.0
    z = (value - mean) / std
    if direction == "up":
        return max(0.0, min(1.0, z / 2.0))
    else:
        return max(0.0, min(1.0, -z / 2.0))


def momentum_score(series, months=6):
    """Risk score [0,1] based on how quickly a series is moving in the risky direction."""
    if not series or len(series) < 2:
        return 0.0
    df = pl.DataFrame(series)
    df = df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
    df = df.sort("date")
    latest_date = df["date"].max()
    cutoff = latest_date - pl.duration(days=months * 30)
    recent = df.filter(pl.col("date") >= cutoff)
    if recent.height < 2:
        return 0.0
    first_val = float(recent[0, "value"])
    last_val = float(recent[-1, "value"])
    change = last_val - first_val
    return abs(change) / (abs(first_val) + 0.001)


def compute_rolling_percentile(series, window_days=3652):
    """Compute the 90th percentile over a rolling ~10-year window using latest data window."""
    if not series:
        return 0
    vals = pl.Series([float(v["value"]) for v in series])
    df = pl.DataFrame(series)
    df = df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
    df = df.sort("date")
    latest = df["date"].max()
    cutoff = latest - pl.duration(days=window_days)
    recent = df.filter(pl.col("date") >= cutoff)
    if recent.height < 10:
        return float(vals.quantile(0.9))
    recent_vals = pl.Series([float(v) for v in recent["value"].to_list()])
    return float(recent_vals.quantile(0.9))


CAPE_API_URL = (
    "https://posix4e.github.io/shiller_wrapper_data/data/stock_market_data.json"
)


def fetch_cape_data():
    """Fetch Shiller CAPE from REST API. Returns [{date, value}, ...]."""
    try:
        response = urllib.request.urlopen(CAPE_API_URL, timeout=15)
        raw = json.loads(response.read().decode())
        records = []
        for entry in raw.get("data", []):
            cape_val = entry.get("cape")
            if cape_val is not None:
                records.append({"date": entry["date_string"], "value": float(cape_val)})
        return monthly_downsample(records)
    except Exception as e:
        print(f"Error fetching Shiller CAPE: {e}")
        return None


def main():
    fred = None
    if FRED_API_KEY:
        try:
            fred = Fred(api_key=FRED_API_KEY)
        except Exception as e:
            print(f"Failed to initialize FRED API: {e}")

    print("Fetching data from FRED...")
    fetched_any_new_data = False

    previous_data = None
    data_path = Path("data/macro_risk.json")
    if data_path.exists():
        try:
            previous_data = json.loads(data_path.read_text())
        except Exception:
            pass

    def fetch_with_fallback(series_id, fallback_key, start_date="1970-01-01"):
        nonlocal fetched_any_new_data
        if not fred:
            return _cached_or_empty(fallback_key)
        try:
            res = get_fred_data_retry(fred, series_id, start_date)
        except Exception as e:
            print(f"Error fetching {series_id}: {e}")
            return _cached_or_empty(fallback_key)

        if res and len(res) > 0:
            fetched_any_new_data = True
            return monthly_downsample(res)
        return _cached_or_empty(fallback_key)

    def _cached_or_empty(key):
        if (
            previous_data
            and "series" in previous_data
            and key in previous_data["series"]
        ):
            print(f"Using cached data for {key}")
            return previous_data["series"][key]
        print(f"No data available for {key}")
        return []

    # 1. Yield Curve
    yield_curve = fetch_with_fallback("T10Y2Y", "yield_curve")

    # 2. Sahm Rule
    sahm_rule = fetch_with_fallback("SAHMREALTIME", "sahm_rule")

    # 3. Recession shading
    recession = fetch_with_fallback("USRECP", "recession")

    # 4. LEI (Leading Economic Index)
    lei = fetch_with_fallback("USSLIND", "lei")

    # 5. Credit spread
    credit_spread = fetch_with_fallback("BAA10Y", "credit_spread")

    # 6. Unemployment (display only)
    unemployment = fetch_with_fallback("UNRATE", "unemployment")

    # 7. Consumer Sentiment (FRED)
    consumer_sentiment = fetch_with_fallback("UMCSENT", "consumer_sentiment")

    # 8. Buffett Indicator
    buffett_indicator = []
    wilshire_s = None
    gdp_s = None

    if fred:
        for series_id in ("WILL5000INDFC", "WILL5000PRFC", "SP500"):
            try:
                wilshire_s = get_fred_data_retry(fred, series_id)
                break
            except Exception:
                continue

    if fred:
        try:
            gdp_s = get_fred_data_retry(fred, "GDP")
        except Exception:
            gdp_s = None

    if wilshire_s is not None and gdp_s is not None:
        fetched_any_new_data = True
        wilshire_df = (
            pl.DataFrame(wilshire_s)
            .rename({"value": "wilshire"})
            .with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
            .drop_nulls()
        )
        gdp_df = (
            pl.DataFrame(gdp_s)
            .rename({"value": "gdp"})
            .with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
            .drop_nulls()
        )

        monthly_wilshire = (
            wilshire_df.with_columns(
                pl.col("date").dt.truncate("1mo").alias("month_start")
            )
            .group_by("month_start")
            .last()
            .sort("month_start")
        )

        monthly_gdp = (
            gdp_df.with_columns(pl.col("date").dt.truncate("1mo").alias("month_start"))
            .group_by("month_start")
            .last()
            .sort("month_start")
        )

        # Upsample GDP to full monthly calendar
        if monthly_gdp.height > 0:
            start = monthly_gdp["month_start"].to_list()[0]
            end = monthly_gdp["month_start"].to_list()[-1]
            date_range = pl.date_range(start, end, "1mo", eager=True)
            full_months = pl.DataFrame({"month_start": date_range})
            monthly_gdp = full_months.join(monthly_gdp, on="month_start", how="left")
            monthly_gdp = monthly_gdp.with_columns(pl.col("gdp").forward_fill())

        buffett_df = monthly_wilshire.join(monthly_gdp, on="month_start", how="inner")
        buffett_df = buffett_df.with_columns(
            (pl.col("wilshire") / pl.col("gdp")).alias("value")
        )
        buffett_df = buffett_df.with_columns(pl.col("date").dt.strftime("%Y-%m-%d"))
        buffett_indicator = buffett_df.select(["date", "value"]).to_dicts()
    else:
        buffett_indicator = _cached_or_empty("buffett_indicator")

    # 9. Shiller CAPE (non-FRED REST API)
    capes = fetch_cape_data()
    if capes is not None and len(capes) > 0:
        fetched_any_new_data = True
        cape = capes
    else:
        cape = _cached_or_empty("cape")

    # --- Risk Score Calculation ---

    def _safe_last(series):
        if not series:
            return None
        v = series[-1]["value"]
        return float(v) if v is not None else None

    current_yc = _safe_last(yield_curve)
    current_sahm = _safe_last(sahm_rule)
    current_buffett = _safe_last(buffett_indicator)
    current_lei = _safe_last(lei)
    current_cs = _safe_last(credit_spread)
    current_cape = _safe_last(cape)
    current_sentiment = _safe_last(consumer_sentiment)

    # Sub-scores: level (continuous 0-1)
    scoring = {}

    scoring["yield_curve"] = {}
    scoring["yield_curve"]["level"] = (
        z_score_sub_score(current_yc, yield_curve, direction="down")
        if current_yc is not None
        else 0.0
    )
    scoring["yield_curve"]["momentum"] = (
        momentum_score(yield_curve, 6) if yield_curve else 0.0
    )
    scoring["yield_curve"]["weight"] = 0.40

    scoring["sahm_rule"] = {}
    scoring["sahm_rule"]["level"] = (
        z_score_sub_score(current_sahm, sahm_rule, direction="up")
        if current_sahm is not None
        else 0.0
    )
    scoring["sahm_rule"]["momentum"] = (
        momentum_score(sahm_rule, 6) if sahm_rule else 0.0
    )
    scoring["sahm_rule"]["weight"] = 0.15

    scoring["buffett_indicator"] = {}
    scoring["buffett_indicator"]["level"] = (
        z_score_sub_score(current_buffett, buffett_indicator, direction="up")
        if current_buffett is not None
        else 0.0
    )
    scoring["buffett_indicator"]["momentum"] = 0.0
    scoring["buffett_indicator"]["weight"] = 0.05

    scoring["lei"] = {}
    scoring["lei"]["level"] = (
        z_score_sub_score(current_lei, lei, direction="down")
        if current_lei is not None
        else 0.0
    )
    scoring["lei"]["momentum"] = momentum_score(lei, 6) if lei else 0.0
    scoring["lei"]["weight"] = 0.12

    scoring["credit_spread"] = {}
    scoring["credit_spread"]["level"] = (
        z_score_sub_score(current_cs, credit_spread, direction="up")
        if current_cs is not None
        else 0.0
    )
    scoring["credit_spread"]["momentum"] = (
        momentum_score(credit_spread, 6) if credit_spread else 0.0
    )
    scoring["credit_spread"]["weight"] = 0.20

    scoring["cape"] = {}
    scoring["cape"]["level"] = (
        z_score_sub_score(current_cape, cape, direction="up")
        if current_cape is not None
        else 0.0
    )
    scoring["cape"]["momentum"] = 0.0
    scoring["cape"]["weight"] = 0.03

    scoring["consumer_sentiment"] = {}
    scoring["consumer_sentiment"]["level"] = (
        z_score_sub_score(current_sentiment, consumer_sentiment, direction="down")
        if current_sentiment is not None
        else 0.0
    )
    scoring["consumer_sentiment"]["momentum"] = (
        momentum_score(consumer_sentiment, 6) if consumer_sentiment else 0.0
    )
    scoring["consumer_sentiment"]["weight"] = 0.05

    # Combine: 70% level + 30% momentum per signal, then weighted sum
    weighted_sum = 0.0
    for key, s in scoring.items():
        combined = 0.7 * s["level"] + 0.3 * s["momentum"]
        weighted_sum += combined * s["weight"]

    # Duration modifier: inversion lasting 6-24 months adds up to +10; past 24 months discounts slightly
    days_inverted = 0
    duration_mod = 0.0
    if yield_curve:
        yc_df = pl.DataFrame(yield_curve)
        yc_df = yc_df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
        yc_df = yc_df.sort("date", descending=True)
        for val in yc_df["value"]:
            if float(val) < 0:
                days_inverted += 1
            else:
                break
        months_inverted = days_inverted / 30.0
        if 6 <= months_inverted <= 24:
            duration_mod = (months_inverted - 6) / 18.0 * 0.10
        elif months_inverted > 24:
            duration_mod = max(0.0, 0.10 - (months_inverted - 24) / 24.0 * 0.05)

    risk_score = round((weighted_sum + duration_mod) * 100)
    risk_score = max(0, min(100, risk_score))

    # Days since inversion (for display)
    days_since_inversion = days_inverted

    output_data = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "risk_score": risk_score,
        "days_since_inversion": days_since_inversion,
        "current_metrics": {
            "sahm_rule": current_sahm,
            "yield_curve": current_yc,
            "buffett_indicator": current_buffett,
            "lei": current_lei,
            "credit_spread": current_cs,
            "unemployment": float(unemployment[-1]["value"]) if unemployment else None,
            "cape": current_cape,
            "consumer_sentiment": current_sentiment,
        },
        "scoring": {
            k: {
                "level": round(v["level"], 4),
                "momentum": round(v["momentum"], 4),
                "weight": v["weight"],
            }
            for k, v in scoring.items()
        },
        "series": {
            "yield_curve": yield_curve,
            "sahm_rule": sahm_rule,
            "buffett_indicator": buffett_indicator,
            "recession": recession,
            "lei": lei,
            "credit_spread": credit_spread,
            "unemployment": unemployment,
            "cape": cape,
            "consumer_sentiment": consumer_sentiment,
        },
    }

    if fetched_any_new_data:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_text(json.dumps(output_data, indent=2))
        print("Data successfully fetched and saved to data/macro_risk.json")
    else:
        print("No new data fetched. File not modified to prevent git diffs.")


if __name__ == "__main__":
    main()
