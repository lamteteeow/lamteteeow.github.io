import json
import os
from datetime import datetime, timedelta, timezone

import polars as pl
from dotenv import load_dotenv
from fredapi import Fred

# Load environment variables
load_dotenv()
FRED_API_KEY = os.getenv("FRED_API_KEY")

if not FRED_API_KEY:
    print(
        "Warning: FRED_API_KEY not found. Using dummy data for demonstration if API fails."
    )


def get_fred_data(fred, series_id, start_date="1970-01-01"):
    if not fred:
        return None
    try:
        data = fred.get_series(series_id, observation_start=start_date)
        # Convert index (dates) and values to a polars DataFrame
        import math

        dates = [d.strftime("%Y-%m-%d") for d in data.index]
        values = [float(v) if not math.isnan(v) else None for v in data.values]
        df = pl.DataFrame({"date": dates, "value": values})
        df = df.drop_nulls()
        df = df.filter(~pl.col("value").is_nan())
        return df.to_dicts()
    except Exception as e:
        print(f"Error fetching {series_id}: {e}")
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

    # Load previous data to fallback on failure
    previous_data = None
    if os.path.exists("data/macro_risk.json"):
        try:
            with open("data/macro_risk.json", "r") as f:
                previous_data = json.load(f)
        except Exception:
            pass

    def get_fred_data_with_fallback(series_id, fallback_key, start_date="1970-01-01"):
        nonlocal fetched_any_new_data
        res = get_fred_data(fred, series_id, start_date)
        if res is not None and len(res) > 0:
            fetched_any_new_data = True
            return res

        # Fallback to cached data
        if (
            previous_data
            and "series" in previous_data
            and fallback_key in previous_data["series"]
        ):
            print(f"Using cached data for {series_id} ({fallback_key})")
            return previous_data["series"][fallback_key]
        return []

    # 1. Yield Curve (T10Y2Y)
    yield_curve = get_fred_data_with_fallback("T10Y2Y", "yield_curve")

    # 2. Sahm Rule (SAHMREALTIME)
    sahm_rule = get_fred_data_with_fallback("SAHMREALTIME", "sahm_rule")

    # 3. Recession Data (USRECP)
    recession = get_fred_data_with_fallback("USRECP", "recession")

    # 4. Buffett Indicator (WILL5000PR / GDP or fallback)
    wilshire_s = None
    if fred:
        try:
            wilshire_s = fred.get_series("WILL5000PR", observation_start="1970-01-01")
        except Exception:
            try:
                wilshire_s = fred.get_series(
                    "WILL5000INDFC", observation_start="1970-01-01"
                )
            except Exception:
                print(
                    "Warning: Wilshire 5000 series not found, falling back to SP500 as proxy."
                )
                try:
                    wilshire_s = fred.get_series(
                        "SP500", observation_start="1970-01-01"
                    )
                except Exception:
                    wilshire_s = None

    gdp_s = None
    if fred:
        try:
            gdp_s = fred.get_series("GDP", observation_start="1970-01-01")
        except Exception:
            gdp_s = None

    import math

    buffett_indicator = []
    if wilshire_s is not None and gdp_s is not None:
        fetched_any_new_data = True
        # Process Wilshire
        wilshire_dates = [d for d in wilshire_s.index]
        wilshire_values = [v if not math.isnan(v) else None for v in wilshire_s.values]
        wilshire_df = pl.DataFrame(
            {"date": wilshire_dates, "wilshire": wilshire_values}
        ).drop_nulls()

        # Process GDP
        gdp_dates = [d for d in gdp_s.index]
        gdp_values = [v if not math.isnan(v) else None for v in gdp_s.values]
        gdp_df = pl.DataFrame({"date": gdp_dates, "gdp": gdp_values}).drop_nulls()

        # Sort, group by month, and get the last value for Wilshire
        monthly_wilshire = (
            wilshire_df.with_columns(
                pl.col("date").dt.truncate("1mo").alias("month_start")
            )
            .group_by("month_start")
            .last()
            .sort("month_start")
        )

        # Sort, group by month, get last value, and forward fill GDP
        monthly_gdp = (
            gdp_df.with_columns(pl.col("date").dt.truncate("1mo").alias("month_start"))
            .group_by("month_start")
            .last()
            .sort("month_start")
            .with_columns(pl.col("gdp").forward_fill())
        )

        # Join Wilshire and GDP
        buffett_df = monthly_wilshire.join(monthly_gdp, on="month_start", how="inner")

        # Calculate ratio
        buffett_df = buffett_df.with_columns(
            (pl.col("wilshire") / pl.col("gdp")).alias("value")
        )

        # Format date back to string
        buffett_df = buffett_df.with_columns(pl.col("date").dt.strftime("%Y-%m-%d"))

        buffett_indicator = buffett_df.select(["date", "value"]).to_dicts()
    else:
        if (
            previous_data
            and "series" in previous_data
            and "buffett_indicator" in previous_data["series"]
        ):
            print("Using cached data for buffett_indicator")
            buffett_indicator = previous_data["series"]["buffett_indicator"]

    # Calculate Risk Score
    risk_points = 0
    max_points = 3

    current_sahm = float(sahm_rule[-1]["value"]) if sahm_rule else 0
    current_yc = float(yield_curve[-1]["value"]) if yield_curve else 0

    if current_sahm >= 0.50:
        risk_points += 1
    if current_yc <= 0.0:
        risk_points += 1

    # Simple threshold for Buffett proxy: if current is > 80th percentile
    if buffett_indicator:
        # Calculate threshold without pandas
        vals = pl.Series([v["value"] for v in buffett_indicator])
        pct_80 = vals.quantile(0.8)
        current_buffett = float(buffett_indicator[-1]["value"])
        if current_buffett > pct_80:
            risk_points += 1

    risk_score = round((risk_points / max_points) * 100)

    # Days since YC inversion
    days_inverted = 0
    if yield_curve:
        yc_df = pl.DataFrame(yield_curve)
        yc_df = yc_df.with_columns(pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"))
        yc_df = yc_df.sort("date", descending=True)

        for val in yc_df["value"]:
            if float(val) < 0:
                days_inverted += 1
            else:
                break

    output_data = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "risk_score": risk_score,
        "days_since_inversion": days_inverted,
        "current_metrics": {"sahm_rule": current_sahm, "yield_curve": current_yc},
        "series": {
            "yield_curve": yield_curve,
            "sahm_rule": sahm_rule,
            "buffett_indicator": buffett_indicator,
            "recession": recession,
        },
    }

    # Only save if we actually fetched at least SOME new data.
    # This ensures that if the API is entirely down or the API key is missing/invalid,
    # we don't overwrite the file with identical data (just to update the timestamp),
    # which would cause unnecessary Git diffs and commits.
    if fetched_any_new_data:
        os.makedirs("data", exist_ok=True)
        with open("data/macro_risk.json", "w") as f:
            json.dump(output_data, f, indent=2)
        print("Data successfully fetched and saved to data/macro_risk.json")
    else:
        print("No new data fetched. File not modified to prevent git diffs.")


if __name__ == "__main__":
    main()
