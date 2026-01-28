# NSE-OHLCV-Data

This repository contains OHLCV (Open–High–Low–Close–Volume) data for selected stocks listed on the National Stock Exchange (NSE).

The data is intended **strictly for academic and research purposes**. We have both daily and hourly data for all stocks on NSE dating back to their IPO.

## Folder Structure

```
NSE_OHLCV_data/
  P1/
    <SYMBOL_1>/
      <SYMBOL_1>_daily.csv
      <SYMBOL_1>_hourly.csv
    <SYMBOL_2>/
      <SYMBOL_2>_daily.csv
      <SYMBOL_2>_hourly.csv
    ...
  P2/
    <SYMBOL_3>/
      <SYMBOL_3>_daily.csv
      <SYMBOL_3>_hourly.csv
    <SYMBOL_4>/
      <SYMBOL_4>_daily.csv
      <SYMBOL_4>_hourly.csv
    ...
  P3/
    <SYMBOL_5>/
      <SYMBOL_5>_daily.csv
      <SYMBOL_5>_hourly.csv
    ...
```

## Data Organization

- **P1**: Contains symbols 1–1000
- **P2**: Contains symbols 1001–2000
- **P3**: Contains symbols 2001+

Each symbol has its own folder with two CSV files:
- `<SYMBOL>_daily.csv`: Daily OHLCV data from listing date to present
- `<SYMBOL>_hourly.csv`: 60-minute OHLCV data from listing date to present

## Data Columns

Both daily and hourly CSV files contain:
- `date`: Timestamp
- `open`: Opening price
- `high`: Highest price
- `low`: Lowest price
- `close`: Closing price
- `volume`: Trading volume

## Usage

This data is provided for academic and research purposes only. Please ensure compliance with applicable regulations and exchange policies when using this data.

## Updates

Data is updated regularly to include the latest trading sessions. 
