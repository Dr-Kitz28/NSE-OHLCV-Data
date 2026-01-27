import pandas as pd

def normalise_dataframe(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    A robust function to clean and normalize yfinance data.
    This function is the single source of truth for data structure.
    """
    if isinstance(df.columns, pd.MultiIndex):
        df_for_symbol = df.loc[:, (slice(None), symbol)]
        df_for_symbol.columns = df_for_symbol.columns.get_level_values(0)
    else:
        df_for_symbol = df

    df_copy = df_for_symbol.copy()

    # Standardize column names
    df_copy.rename(
        columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
        },
        inplace=True,
    )

    # Ensure all required columns exist
    for col in ["open", "high", "low", "close", "adj_close", "volume"]:
        if col not in df_copy.columns:
            df_copy[col] = pd.NA

    # Coerce to numeric, this is critical
    price_cols = ["open", "high", "low", "close", "adj_close"]
    df_copy[price_cols] = df_copy[price_cols].apply(pd.to_numeric, errors='coerce')

    # Enforce OHLC relationships
    df_copy["high"] = df_copy[["high", "open", "close"]].max(axis=1)
    df_copy["low"] = df_copy[["low", "open", "close"]].min(axis=1)

    # CRITICAL: Clean adj_close before using it for calculations.
    # Set non-positive values to NaN, then fill forward, then backfill.
    df_copy.loc[df_copy["adj_close"] <= 0, "adj_close"] = pd.NA
    # Avoid chained inplace by reassigning the result
    df_copy["adj_close"] = df_copy["adj_close"].ffill()
    df_copy["adj_close"] = df_copy["adj_close"].bfill()
    df_copy["adj_close"] = df_copy["adj_close"].fillna(0) # Fill any remaining NaNs with 0

    # Round price columns to a reasonable precision AFTER cleaning.
    df_copy[price_cols] = df_copy[price_cols].round(4)

    # Calculate pct_change using the cleaned and rounded adj_close
    df_copy["pct_change"] = df_copy["adj_close"].pct_change(fill_method=None) * 100

    # Add symbol and date columns
    df_copy["symbol"] = symbol
    if "date" in df_copy.columns and pd.api.types.is_datetime64_any_dtype(df_copy["date"]):
        date_series = df_copy["date"]
    else:
        date_series = df_copy.index.to_series()

    if date_series.dt.tz is None:
        df_copy["date"] = date_series.dt.tz_localize("UTC").dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    else:
        df_copy["date"] = date_series.dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%S%z")

    # Clean up volume
    df_copy["volume"] = pd.to_numeric(df_copy["volume"], errors="coerce").fillna(0).astype(int)

    # Final column order
    final_cols = [
        "symbol", "date", "open", "high", "low", "close",
        "adj_close", "volume", "pct_change"
    ]
    return df_copy[final_cols]
