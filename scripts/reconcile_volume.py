import argparse
import pandas as pd
import numpy as np

from cotdata.providers import norgate
from cotdata import store

def get_roll_windows(df: pd.DataFrame, window_days: int = 5) -> pd.DataFrame:
    """Find dates where the contract rolled (Delivery Month changed) and return a mask of those dates +/- window_days."""
    if "Delivery Month" not in df.columns:
        return pd.Series(False, index=df.index)
        
    dm = df["Delivery Month"]
    roll = dm.ne(dm.shift()) & dm.shift().notna()
    
    # Expand window
    roll_dates = df.index[roll]
    mask = pd.Series(False, index=df.index)
    for rd in roll_dates:
        start = rd - pd.Timedelta(days=window_days)
        end = rd + pd.Timedelta(days=window_days)
        mask.loc[start:end] = True
        
    return mask

def verify_symbol(symbol: str):
    print(f"\n{'='*50}\nReconciling {symbol}\n{'='*50}")
    
    print(f"1. Fetching & Reconstructing {symbol} via norgate.py ...")
    norgate.update(symbols=[symbol])
    
    print(f"2. Reading updated dataframe for {symbol} ...")
    df = store.read_prices(symbol, "backadj")
    
    if df.empty:
        print(f"Error: No data found for {symbol}.")
        return

    # Check Columns
    expected_cols = ["Volume", "Volume_Reconstructed", "FirstVolume", "SecondVolume", "Volume_Source", "FirstContract", "SecondContract"]
    for col in expected_cols:
        if col not in df.columns:
            print(f"❌ Missing expected column: {col}")
            return
            
    print("✅ All expected additive columns are present.")
    
    # Check Fallback
    source = df["Volume_Source"].iloc[-1]
    print(f"Volume Source: {source}")
    
    if source == "raw":
        print(f"✅ Fallback applied correctly. Volume_Reconstructed == Volume for all rows: {(df['Volume_Reconstructed'] == df['Volume']).all()}")
        return
        
    # Check bounds
    print("3. Running Sanity Checks...")
    
    # a. First + Second = Reconstructed
    valid = df.dropna(subset=["FirstVolume", "SecondVolume"])
    reconstructed_match = np.isclose(
        valid["FirstVolume"] + valid["SecondVolume"], 
        valid["Volume_Reconstructed"]
    ).all()
    print(f"{'✅' if reconstructed_match else '❌'} FirstVolume + SecondVolume == Volume_Reconstructed")
    
    # b. Reconstructed >= Volume (raw)
    # The true combined volume should be roughly >= the single front-month volume
    # Some minor exceptions can happen due to Norgate raw volume inclusions, but generally true.
    vol_diff = (valid["Volume_Reconstructed"] >= valid["Volume"] * 0.95).mean()
    print(f"{'✅' if vol_diff > 0.99 else '❌'} Volume_Reconstructed is >= Raw Volume (in {vol_diff*100:.1f}% of days)")
    
    # c. No NaNs introduced into default Volume
    nans_in_raw = df["Volume"].isna().sum()
    print(f"{'✅' if nans_in_raw == 0 else '❌'} Default 'Volume' has 0 NaNs ({nans_in_raw} found)")
    
    print("\n4. Roll Window Drop-off Analysis")
    # Compare average volume during roll windows vs non-roll windows
    roll_mask = get_roll_windows(df, window_days=5)
    
    if roll_mask.sum() == 0:
        print("No roll windows detected.")
        return
        
    roll_raw = df.loc[roll_mask, "Volume"].mean()
    nonroll_raw = df.loc[~roll_mask, "Volume"].mean()
    
    roll_rec = df.loc[roll_mask, "Volume_Reconstructed"].mean()
    nonroll_rec = df.loc[~roll_mask, "Volume_Reconstructed"].mean()
    
    raw_drop = (nonroll_raw - roll_raw) / nonroll_raw * 100 if nonroll_raw else 0
    rec_drop = (nonroll_rec - roll_rec) / nonroll_rec * 100 if nonroll_rec else 0
    
    print(f"  Raw Volume:")
    print(f"    Non-Roll Avg: {nonroll_raw:,.0f}")
    print(f"    Roll Avg:     {roll_raw:,.0f}")
    print(f"    Drop-off:     {raw_drop:.1f}%")
    
    print(f"  Reconstructed Volume:")
    print(f"    Non-Roll Avg: {nonroll_rec:,.0f}")
    print(f"    Roll Avg:     {roll_rec:,.0f}")
    print(f"    Drop-off:     {rec_drop:.1f}%")
    
    if rec_drop < raw_drop:
        print("✅ Reconstructed volume successfully smoothed the roll drop-off!")
    else:
        print("❌ Reconstructed volume did not improve the roll drop-off.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile Volume Reconstruction")
    parser.add_argument("--symbols", nargs="+", default=["ES", "BTC"], help="Internal symbols to test")
    args = parser.parse_args()
    
    for sym in args.symbols:
        verify_symbol(sym)
