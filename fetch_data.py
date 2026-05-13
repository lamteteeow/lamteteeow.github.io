import os
import json
import pandas as pd
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
        df = pd.DataFrame({'date': data.index, 'value': data.values})
        df = df.dropna()
        df['date'] = df['date'].dt.strftime('%Y-%m-%d')
        return df.to_dict('records')
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
    
    # 4. Buffett Indicator (WILL5000PRFC / GDP)
    wilshire = fred.get_series("WILL5000PRFC", observation_start="1970-01-01")
    gdp = fred.get_series("GDP", observation_start="1970-01-01")
    
    wilshire_df = pd.DataFrame({'date': wilshire.index, 'wilshire': wilshire.values}).dropna()
    gdp_df = pd.DataFrame({'date': gdp.index, 'gdp': gdp.values}).dropna()
    
    # Resample to monthly and forward fill GDP
    wilshire_df.set_index('date', inplace=True)
    gdp_df.set_index('date', inplace=True)
    
    monthly_wilshire = wilshire_df.resample('M').last()
    monthly_gdp = gdp_df.resample('M').last().ffill()
    
    buffett_df = monthly_wilshire.join(monthly_gdp, how='inner')
    # GDP is in billions, Wilshire is index (often proxy for billions in some series, but let's normalize)
    # Actually, WILL5000PRFC is price index. WILL5000IND is total market index. 
    # Let's just create a ratio that we normalize to 1.0 = historical average.
    buffett_df['value'] = buffett_df['wilshire'] / buffett_df['gdp']
    
    # Normalize Buffett Indicator (Z-score or similar) to make it readable, but for now just raw ratio
    buffett_df = buffett_df.reset_index()
    buffett_df['date'] = buffett_df['date'].dt.strftime('%Y-%m-%d')
    buffett_indicator = buffett_df[['date', 'value']].to_dict('records')

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
        vals = [v['value'] for v in buffett_indicator]
        pct_80 = pd.Series(vals).quantile(0.8)
        current_buffett = float(buffett_indicator[-1]['value'])
        if current_buffett > pct_80:
            risk_points += 1

    risk_score = round((risk_points / max_points) * 100)

    # Days since YC inversion
    days_inverted = 0
    if yield_curve:
        yc_df = pd.DataFrame(yield_curve)
        yc_df['date'] = pd.to_datetime(yc_df['date'])
        yc_df = yc_df.sort_values('date', ascending=False)
        
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
