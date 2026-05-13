import os
import json
import polars as pl
from datetime import datetime, timedelta
from fredapi import Fred
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
FRED_API_KEY = os.getenv("FRED_API_KEY")

if not FRED_API_KEY:
    print("Warning: FRED_API_KEY not found. Using dummy data for demonstration if API fails.")

def get_fred_data(fred, series_id, start_date="1970-01-01"):
    try:
        data = fred.get_series(series_id, observation_start=start_date)
        # Convert index (dates) and values to a polars DataFrame
        dates = [d.strftime('%Y-%m-%d') for d in data.index]
        values = data.values
        df = pl.DataFrame({'date': dates, 'value': values})
        df = df.drop_nulls()
        return df.to_dicts()
    except Exception as e:
        print(f"Error fetching {series_id}: {e}")
        return []

def main():
    try:
        fred = Fred(api_key=FRED_API_KEY)
    except Exception as e:
        print(f"Failed to initialize FRED API: {e}")
        return

    print("Fetching data from FRED...")
    
    # 1. Yield Curve (T10Y2Y)
    yield_curve = get_fred_data(fred, "T10Y2Y")
    
    # 2. Sahm Rule (SAHMREALTIME)
    sahm_rule = get_fred_data(fred, "SAHMREALTIME")
    
    # 3. Recession Data (USRECP)
    recession = get_fred_data(fred, "USRECP")
    
    # 4. Buffett Indicator (WILL5000PR / GDP or fallback)
    try:
        wilshire_s = fred.get_series("WILL5000PR", observation_start="1970-01-01")
    except ValueError:
        try:
            wilshire_s = fred.get_series("WILL5000INDFC", observation_start="1970-01-01")
        except ValueError:
            print("Warning: Wilshire 5000 series not found, falling back to SP500 as proxy.")
            wilshire_s = fred.get_series("SP500", observation_start="1970-01-01")
            
    try:
        gdp_s = fred.get_series("GDP", observation_start="1970-01-01")
    except ValueError:
        gdp_s = []

    # Process Wilshire
    wilshire_dates = [d for d in wilshire_s.index]
    wilshire_df = pl.DataFrame({'date': wilshire_dates, 'wilshire': wilshire_s.values}).drop_nulls()
    
    # Process GDP
    gdp_dates = [d for d in gdp_s.index]
    gdp_df = pl.DataFrame({'date': gdp_dates, 'gdp': gdp_s.values}).drop_nulls()

    # Sort, group by month, and get the last value for Wilshire
    monthly_wilshire = (
        wilshire_df
        .with_columns(pl.col("date").dt.truncate("1mo").alias("month_start"))
        .group_by("month_start")
        .last()
        .sort("month_start")
    )
    
    # Sort, group by month, get last value, and forward fill GDP
    monthly_gdp = (
        gdp_df
        .with_columns(pl.col("date").dt.truncate("1mo").alias("month_start"))
        .group_by("month_start")
        .last()
        .sort("month_start")
        .with_columns(pl.col("gdp").forward_fill())
    )

    # Join Wilshire and GDP
    buffett_df = monthly_wilshire.join(monthly_gdp, on="month_start", how="inner")
    
    # Calculate ratio
    buffett_df = buffett_df.with_columns((pl.col("wilshire") / pl.col("gdp")).alias("value"))
    
    # Format date back to string
    buffett_df = buffett_df.with_columns(pl.col("date").dt.strftime("%Y-%m-%d"))
    
    buffett_indicator = buffett_df.select(["date", "value"]).to_dicts()

    # Calculate Risk Score
    risk_points = 0
    max_points = 3
    
    current_sahm = float(sahm_rule[-1]['value']) if sahm_rule else 0
    current_yc = float(yield_curve[-1]['value']) if yield_curve else 0
    
    if current_sahm >= 0.50:
        risk_points += 1
    if current_yc <= 0.0:
        risk_points += 1
        
    # Simple threshold for Buffett proxy: if current is > 80th percentile
    if buffett_indicator:
        # Calculate threshold without pandas
        vals = pl.Series([v['value'] for v in buffett_indicator])
        pct_80 = vals.quantile(0.8)
        current_buffett = float(buffett_indicator[-1]['value'])
        if current_buffett > pct_80:
            risk_points += 1

    risk_score = round((risk_points / max_points) * 100)

    # Days since YC inversion
    days_inverted = 0
    if yield_curve:
        yc_df = pl.DataFrame(yield_curve)
        yc_df = yc_df.with_columns(pl.col('date').str.strptime(pl.Date, "%Y-%m-%d"))
        yc_df = yc_df.sort('date', descending=True)
        
        for val in yc_df['value']:
            if float(val) < 0:
                days_inverted += 1
            else:
                break

    output_data = {
        "last_updated": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        "risk_score": risk_score,
        "days_since_inversion": days_inverted,
        "current_metrics": {
            "sahm_rule": current_sahm,
            "yield_curve": current_yc
        },
        "series": {
            "yield_curve": yield_curve,
            "sahm_rule": sahm_rule,
            "buffett_indicator": buffett_indicator,
            "recession": recession
        }
    }

    # Save to data/macro_risk.json
    os.makedirs("data", exist_ok=True)
    with open("data/macro_risk.json", "w") as f:
        json.dump(output_data, f, indent=2)
    
    print("Data successfully fetched and saved to data/macro_risk.json")

if __name__ == "__main__":
    main()
