"""Core statistics generation engine for NSE stock datasets.

This module loads per-symbol daily and hourly datasets and produces the
distribution, streak, and probability artefacts requested by the analytics
pipeline. It is designed to be orchestrated by the CLI in
``tools/generate_statistics.py`` but can also be consumed programmatically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import matplotlib
import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
import os

# If set to a truthy value, plotting/PNG generation is skipped to speed
# large batch runs. Workers inherit this env var from the parent process
# so setting `GOLDENEYE_NO_PLOTS=1` before running the generator will
# avoid creating images.
NO_PLOTS = bool(os.environ.get("GOLDENEYE_NO_PLOTS"))
from pandas.api.types import is_datetime64tz_dtype

matplotlib.use("Agg")


PERCENT_RANGE = (-20.0, 20.0)
PERCENT_STEP = 0.1  # percentage points
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
HOURLY_SLOTS = 6


@dataclass(slots=True)
class DatasetBundle:
    """Container for per-symbol data frames."""

    daily: Optional[pd.DataFrame]
    hourly: Optional[pd.DataFrame]


@dataclass(slots=True)
class DistributionResult:
    histogram: pd.DataFrame
    stats: Dict[str, float]


@dataclass(slots=True)
class ProbabilityResult:
    pdf: pd.DataFrame
    pdd: pd.DataFrame


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_symbol_datasets(symbol_dir: Path) -> DatasetBundle:
    """Load the daily and hourly CSVs for *symbol_dir* if they exist."""
    # Support multiple filename conventions. Prefer pre-computed complete files
    # but fall back to legacy *_daily.csv / *_hourly.csv names found in the
    # repository.
    daily_patterns = ["*_complete_with_pct.csv", "*_complete_with_pct*.csv", "*_daily.csv", "*daily.csv"]
    hourly_patterns = ["*_complete_historical_1h_730days.csv", "*_hourly.csv", "*hourly.csv"]

    daily_path = None
    hourly_path = None

    for pat in daily_patterns:
        candidate = next(symbol_dir.glob(pat), None)
        if candidate is not None:
            daily_path = candidate
            break

    for pat in hourly_patterns:
        candidate = next(symbol_dir.glob(pat), None)
        if candidate is not None:
            hourly_path = candidate
            break

    daily_df: Optional[pd.DataFrame] = None
    hourly_df: Optional[pd.DataFrame] = None

    if daily_path and daily_path.exists():
        try:
            daily_df = pd.read_csv(daily_path)
        except Exception:
            daily_df = None

        if daily_df is not None and "date" in daily_df.columns:
            daily_df["date"] = pd.to_datetime(daily_df["date"], utc=True, errors="coerce")
        # Ensure pct_change exists; compute from price columns when missing.
        if daily_df is not None:
            if "pct_change" in daily_df.columns:
                daily_df["pct_change"] = pd.to_numeric(daily_df["pct_change"], errors="coerce")
            else:
                price_source = next((col for col in ("adj_close", "close", "open") if col in daily_df.columns), None)
                if price_source is not None:
                    price_series = pd.to_numeric(daily_df[price_source], errors="coerce")
                    try:
                        pct = price_series.pct_change(fill_method=None)
                    except TypeError:
                        pct = price_series.pct_change()
                    daily_df["pct_change"] = pct * 100

    if hourly_path and hourly_path.exists():
        try:
            hourly_df = _prepare_hourly_dataframe(pd.read_csv(hourly_path))
        except Exception:
            hourly_df = None

    return DatasetBundle(daily=daily_df, hourly=hourly_df)


def _prepare_hourly_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise legacy hourly datasets to the expected schema.

    Historically some symbol files used ``timestamp`` (or other aliases)
    instead of ``date`` and omitted the pre-computed ``pct_change`` column.
    This helper harmonises those variants so the downstream statistics
    pipeline can operate without special cases.
    """

    if df is None or df.empty:
        return df

    frame = df.copy()

    datetime_columns = (
        "date",
        "timestamp",
        "Timestamp",
        "datetime",
        "Datetime",
        "datetime_utc",
        "DatetimeUTC",
    )

    datetime_source = next((col for col in datetime_columns if col in frame.columns), None)
    if datetime_source is not None:
        frame["date"] = pd.to_datetime(frame[datetime_source], utc=True, errors="coerce")
    else:
        frame["date"] = pd.NaT

    numeric_columns = ("open", "high", "low", "close", "adj_close", "volume", "pct_change")
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    if "pct_change" not in frame.columns or frame["pct_change"].dropna().empty:
        price_source = next((col for col in ("adj_close", "close", "open") if col in frame.columns), None)
        if price_source is not None:
            price_series = pd.to_numeric(frame[price_source], errors="coerce")
            try:
                pct = price_series.pct_change(fill_method=None)
            except TypeError:  # pandas < 2.1 fallback
                pct = price_series.pct_change()
            frame["pct_change"] = pct * 100

    frame = frame.sort_values(by="date")
    frame = frame[frame["date"].notna()].copy()
    frame.reset_index(drop=True, inplace=True)

    return frame


def _percent_bin_edges() -> np.ndarray:
    start, end = PERCENT_RANGE
    return np.arange(start, end + PERCENT_STEP, PERCENT_STEP)


def _histogram_from_series(series: pd.Series, *, dropna: bool = True) -> pd.DataFrame:
    if dropna:
        values = series.dropna().to_numpy()
    else:
        values = series.to_numpy()

    if values.size == 0:
        bins = _percent_bin_edges()
        centers = (bins[:-1] + bins[1:]) / 2
        zeros = np.zeros_like(centers, dtype=int)
        histogram = pd.DataFrame({"percent": centers, "frequency": zeros})
        histogram["probability"] = 0.0
        return histogram

    bins = _percent_bin_edges()
    counts, edges = np.histogram(values, bins=bins, range=PERCENT_RANGE)
    centers = (edges[:-1] + edges[1:]) / 2
    histogram = pd.DataFrame({"percent": centers, "frequency": counts.astype(int)})
    histogram["probability"] = histogram["frequency"] / histogram["frequency"].sum() if histogram["frequency"].sum() else 0
    return histogram


def _describe_series(series: pd.Series) -> Dict[str, float]:
    cleaned = series.dropna()
    if cleaned.empty:
        return {
            "count": 0,
            "mean": math.nan,
            "std": math.nan,
            "min": math.nan,
            "max": math.nan,
            "median": math.nan,
            "skew": math.nan,
            "kurtosis": math.nan,
        }

    return {
        "count": int(cleaned.count()),
        "mean": float(cleaned.mean()),
        "std": float(cleaned.std(ddof=0)),
        "min": float(cleaned.min()),
        "max": float(cleaned.max()),
        "median": float(cleaned.median()),
        "skew": float(cleaned.skew()),
        "kurtosis": float(cleaned.kurtosis()),
    }


def _weekday_name_index(dt_index: pd.Series) -> pd.Series:
    series = dt_index
    if is_datetime64tz_dtype(series):
        # Convert timezone-aware timestamps to the trading locale before
        # deriving weekday labels so we don't misclassify late-session bars.
        series = series.dt.tz_convert("Asia/Kolkata")
    return series.dt.day_name()


def _localise_hourly(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    localized = df.copy()
    localized["date_local"] = localized["date"].dt.tz_convert("Asia/Kolkata")
    localized["weekday"] = localized["date_local"].dt.day_name()
    localized["time"] = localized["date_local"].dt.strftime("%H:%M")
    localized["slot"] = _assign_hour_slots(localized)
    localized["hour_label"] = localized.apply(_hour_label_from_row, axis=1)
    return localized


def _assign_hour_slots(df: pd.DataFrame) -> pd.Series:
    times = df["time"]
    volume = df.get("volume")
    selector = pd.Series(True, index=times.index)
    if volume is not None:
        selector &= volume.fillna(0) > 0

    candidate_times = (
        df.loc[selector, "time"].value_counts().sort_index(ascending=True).index.tolist()
    )
    if len(candidate_times) < HOURLY_SLOTS:
        candidate_times = times.value_counts().sort_index().index.tolist()

    candidate_times = sorted(candidate_times)
    candidate_times = candidate_times[:HOURLY_SLOTS]

    mapping = {time: idx + 1 for idx, time in enumerate(candidate_times)}
    return times.map(mapping)


def _format_stat_value(value: float | int | None) -> str:
    if value is None:
        return "NaN"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _format_stats_block(stats: Optional[Dict[str, float]]) -> Optional[str]:
    if not stats:
        return None
    count = stats.get("count", 0)
    if count is None or count == 0:
        return "Count: 0"
    lines = [
        f"Count: {_format_stat_value(count)}",
        f"Mean: {_format_stat_value(stats.get('mean'))}",
        f"Std: {_format_stat_value(stats.get('std'))}",
        f"Skew: {_format_stat_value(stats.get('skew'))}",
        f"Kurtosis: {_format_stat_value(stats.get('kurtosis'))}",
    ]
    return "\n".join(lines)


def _plot_distribution(
    histogram: pd.DataFrame,
    title: str,
    output_path: Path,
    *,
    color: str = "steelblue",
    stats: Optional[Dict[str, float]] = None,
) -> None:
    if NO_PLOTS:
        # still ensure directory for CSVs that may be saved elsewhere
        _ensure_directory(output_path.parent)
        return
    _ensure_directory(output_path.parent)
    fig, ax = plt.subplots(figsize=(12, 6))
    try:
        ax.bar(histogram["percent"], histogram["frequency"], width=PERCENT_STEP * 0.8, color=color)
        ax.set_title(title)
        ax.set_xlabel("% change")
        ax.set_ylabel("Frequency")
        ax.set_xlim(PERCENT_RANGE)
        ax.grid(axis="y", alpha=0.3)

        block = _format_stats_block(stats)
        if block:
            ax.text(
                0.98,
                0.98,
                block,
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=9,
                bbox={"boxstyle": "round,pad=0.4", "facecolor": "white", "alpha": 0.8},
            )

        fig.savefig(output_path, bbox_inches="tight")
    finally:
        plt.close(fig)


def _plot_signed_bar(
    frame: pd.DataFrame,
    title: str,
    output_path: Path,
    *,
    x_col: str,
    y_col: str,
    direction_col: str,
    stats: Optional[Dict[str, float]] = None,
) -> None:
    if NO_PLOTS:
        _ensure_directory(output_path.parent)
        return
    _ensure_directory(output_path.parent)
    fig, ax = plt.subplots(figsize=(12, 6))
    try:
        positive = frame[frame[direction_col] == "Up"].sort_values(by=x_col)
        negative = frame[frame[direction_col] == "Down"].sort_values(by=x_col)

        if not positive.empty:
            ax.bar(positive[x_col], positive[y_col], color="seagreen", label="Up")
        if not negative.empty:
            ax.bar(negative[x_col], -negative[y_col], color="indianred", label="Down")
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel(x_col)
        ax.set_ylabel("Frequency")
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.3)
        if not positive.empty or not negative.empty:
            ax.legend()

        block = _format_stats_block(stats)
        if block:
            ax.text(
                0.02,
                0.98,
                block,
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=9,
                bbox={"boxstyle": "round,pad=0.4", "facecolor": "white", "alpha": 0.8},
            )

        fig.savefig(output_path, bbox_inches="tight")
    finally:
        plt.close(fig)


def _save_csv(frame: pd.DataFrame, path: Path) -> None:
    _ensure_directory(path.parent)
    frame.to_csv(path, index=False)


def _format_hour_label(slot: int, time_label: str | None) -> str:
    if time_label:
        return f"H{slot}_{time_label.replace(':', '')}"
    return f"H{slot}"


def _hour_label_from_row(row: pd.Series) -> str:
    slot = row.get("slot")
    time_label = row.get("time")
    return _format_hour_label(int(slot) if not pd.isna(slot) else 0, time_label if isinstance(time_label, str) else None)


def generate_hourly_distributions(symbol: str, hourly_df: pd.DataFrame, output_root: Path) -> pd.DataFrame:
    """Generate 30 hourly distribution CSVs + plots and return summary stats."""

    if hourly_df is None or hourly_df.empty:
        return pd.DataFrame(columns=["Weekday", "Hour", "Count", "Mean", "Std", "Min", "Max", "Median", "Skew", "Kurtosis"])

    enriched = _localise_hourly(hourly_df)
    enriched = enriched.dropna(subset=["slot", "weekday", "pct_change"])

    summary_records: List[Dict[str, object]] = []
    base_dir = output_root / "hourly" / "distributions"
    _ensure_directory(base_dir)

    for weekday in WEEKDAYS:
        weekday_df = enriched[enriched["weekday"] == weekday]
        _ensure_directory(base_dir / weekday)

        for slot in range(1, HOURLY_SLOTS + 1):
            slot_df = weekday_df[weekday_df["slot"] == slot] if not weekday_df.empty else weekday_df
            if slot_df is None or slot_df.empty:
                pct_series = pd.Series(dtype=float)
            else:
                slot_df = slot_df.sort_values(by="date")
                pct_series = slot_df["pct_change"]

            label_candidates = slot_df["hour_label"].dropna().mode() if not slot_df.empty else pd.Series(dtype=str)
            label = label_candidates.iloc[0] if not label_candidates.empty else _format_hour_label(slot, None)
            histogram = _histogram_from_series(pct_series)
            stats = _describe_series(pct_series)
            summary_records.append(
                {
                    "Weekday": weekday,
                    "Hour": label,
                    "Count": stats["count"],
                    "Mean": stats["mean"],
                    "Std": stats["std"],
                    "Min": stats["min"],
                    "Max": stats["max"],
                    "Median": stats["median"],
                    "Skew": stats["skew"],
                    "Kurtosis": stats["kurtosis"],
                }
            )

            csv_path = base_dir / weekday / f"{symbol}_{weekday}_{label}.csv"
            plot_path = base_dir / weekday / f"{symbol}_{weekday}_{label}.png"
            _save_csv(histogram, csv_path)
            _plot_distribution(
                histogram,
                f"{symbol} {weekday} {label} hourly distribution",
                plot_path,
                stats=stats,
            )

    summary = pd.DataFrame(summary_records)
    if not summary.empty:
        summary = summary.sort_values(by=["Weekday", "Hour"])
    _save_csv(summary, output_root / "hourly" / f"{symbol}_hourly_distribution_summary.csv")
    return summary


def generate_daily_distributions(symbol: str, daily_df: pd.DataFrame, output_root: Path) -> pd.DataFrame:
    if daily_df is None or daily_df.empty:
        return pd.DataFrame(columns=["Weekday", "Count", "Mean", "Std", "Min", "Max", "Median", "Skew", "Kurtosis"])

    frame = daily_df.copy()
    frame = frame.dropna(subset=["pct_change", "date"])
    frame["weekday"] = _weekday_name_index(frame["date"])

    base_dir = output_root / "daily" / "distributions"
    _ensure_directory(base_dir)
    summary_records: List[Dict[str, object]] = []

    for weekday in WEEKDAYS:
        subset = frame[frame["weekday"] == weekday]
        _ensure_directory(base_dir / weekday)
        pct_series = subset["pct_change"] if not subset.empty else pd.Series(dtype=float)

        histogram = _histogram_from_series(pct_series)
        stats = _describe_series(pct_series)
        summary_records.append(
            {
                "Weekday": weekday,
                "Count": stats["count"],
                "Mean": stats["mean"],
                "Std": stats["std"],
                "Min": stats["min"],
                "Max": stats["max"],
                "Median": stats["median"],
                "Skew": stats["skew"],
                "Kurtosis": stats["kurtosis"],
            }
        )

        csv_path = base_dir / weekday / f"{symbol}_{weekday}_distribution.csv"
        plot_path = base_dir / weekday / f"{symbol}_{weekday}_distribution.png"
        _save_csv(histogram, csv_path)
        _plot_distribution(
            histogram,
            f"{symbol} {weekday} daily distribution",
            plot_path,
            stats=stats,
        )

    summary = pd.DataFrame(summary_records)
    if not summary.empty:
        summary = summary.sort_values(by="Weekday")
    _save_csv(summary, output_root / "daily" / f"{symbol}_daily_distribution_summary.csv")
    return summary


def _compute_streaks(values: Iterable[float]) -> List[Tuple[str, int]]:
    streaks: List[Tuple[str, int]] = []
    current_direction: Optional[str] = None
    current_length = 0

    for value in values:
        if pd.isna(value) or value == 0:
            if current_direction is not None and current_length:
                streaks.append((current_direction, current_length))
            current_direction = None
            current_length = 0
            continue

        direction = "Up" if value > 0 else "Down"
        if direction == current_direction:
            current_length += 1
        else:
            if current_direction is not None and current_length:
                streaks.append((current_direction, current_length))
            current_direction = direction
            current_length = 1

    if current_direction is not None and current_length:
        streaks.append((current_direction, current_length))

    return streaks


def generate_weekday_streaks(symbol: str, daily_df: pd.DataFrame, output_root: Path) -> pd.DataFrame:
    if daily_df is None or daily_df.empty:
        return pd.DataFrame(columns=["Weekday", "Direction", "Streak Length", "Frequency", "Relative Frequency", "Total Runs"])

    frame = daily_df.dropna(subset=["pct_change", "date"]).copy()
    frame["weekday"] = _weekday_name_index(frame["date"])

    records: List[Dict[str, object]] = []
    base_dir = output_root / "daily" / "streaks" / "weekday"
    _ensure_directory(base_dir)

    columns = ["Weekday", "Direction", "Streak Length", "Frequency", "Relative Frequency", "Total Runs"]

    for weekday in WEEKDAYS:
        subset = frame[frame["weekday"] == weekday].sort_values(by="date")
        _ensure_directory(base_dir / weekday)
        weekday_records: List[Dict[str, object]] = []

        if not subset.empty:
            streaks = _compute_streaks(subset["pct_change"].tolist())
            counts: Dict[Tuple[str, int], int] = {}
            totals: Dict[str, int] = {"Up": 0, "Down": 0}

            for direction, length in streaks:
                key = (direction, length)
                counts[key] = counts.get(key, 0) + 1
                totals[direction] += 1

            for (direction, length), frequency in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1])):
                total_runs = totals[direction]
                relative = frequency / total_runs if total_runs else 0.0
                record = {
                    "Weekday": weekday,
                    "Direction": direction,
                    "Streak Length": int(length),
                    "Frequency": int(frequency),
                    "Relative Frequency": relative,
                    "Total Runs": int(total_runs),
                }
                weekday_records.append(record)
                records.append(record)

        weekday_frame = pd.DataFrame(weekday_records, columns=columns)
        csv_path = base_dir / weekday / f"{symbol}_{weekday}_streaks.csv"
        plot_path = base_dir / weekday / f"{symbol}_{weekday}_streaks.png"
        _save_csv(weekday_frame, csv_path)
        freq_series = weekday_frame["Frequency"] if not weekday_frame.empty else pd.Series(dtype=float)
        _plot_signed_bar(
            weekday_frame,
            f"{symbol} {weekday} streak distribution",
            plot_path,
            x_col="Streak Length",
            y_col="Frequency",
            direction_col="Direction",
            stats=_describe_series(freq_series),
        )

    master = pd.DataFrame(records)
    _save_csv(master, output_root / "daily" / "streaks" / f"{symbol}_weekday_streaks.csv")
    return master


def generate_continuous_streaks(symbol: str, df: pd.DataFrame, output_root: Path, *, label: str) -> pd.DataFrame:
    columns = ["Direction", "Streak Length", "Frequency", "Relative Frequency", "Total Runs"]

    if df is None or df.empty:
        frame_out = pd.DataFrame(columns=columns)
    else:
        frame = df.dropna(subset=["pct_change"]).sort_values(by="date")
        streaks = _compute_streaks(frame["pct_change"].tolist())

        counts: Dict[Tuple[str, int], int] = {}
        totals: Dict[str, int] = {"Up": 0, "Down": 0}

        for direction, length in streaks:
            key = (direction, length)
            counts[key] = counts.get(key, 0) + 1
            totals[direction] += 1

        records = []
        for (direction, length), frequency in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1])):
            total_runs = totals[direction]
            records.append(
                {
                    "Direction": direction,
                    "Streak Length": int(length),
                    "Frequency": int(frequency),
                    "Relative Frequency": frequency / total_runs if total_runs else 0.0,
                    "Total Runs": int(total_runs),
                }
            )

        frame_out = pd.DataFrame(records, columns=columns)

    csv_path = output_root / label / f"{symbol}_{label}_streaks.csv"
    plot_path = output_root / label / f"{symbol}_{label}_streaks.png"
    _save_csv(frame_out, csv_path)
    freq_series = frame_out["Frequency"] if not frame_out.empty else pd.Series(dtype=float)
    _plot_signed_bar(
        frame_out,
        f"{symbol} {label} streak distribution",
        plot_path,
        x_col="Streak Length",
        y_col="Frequency",
        direction_col="Direction",
        stats=_describe_series(freq_series),
    )
    return frame_out


def _conditional_probability(sequence: List[float]) -> Dict[str, float]:
    if len(sequence) < 2:
        return {"Up": math.nan, "Down": math.nan}

    current = sequence[1:]
    previous = sequence[:-1]

    current = pd.Series(current)
    previous = pd.Series(previous)

    mask_prev_up = previous > 0
    mask_prev_down = previous < 0

    denom_up = mask_prev_up.sum()
    denom_down = mask_prev_down.sum()

    prob_up = float(((current > 0) & mask_prev_up).sum() / denom_up) if denom_up else math.nan
    prob_down = float(((current < 0) & mask_prev_down).sum() / denom_down) if denom_down else math.nan

    return {"Up": prob_up, "Down": prob_down}


def generate_conditional_probabilities(
    symbol: str,
    df: pd.DataFrame,
    output_root: Path,
    *,
    mode: str,
    group_keys: Sequence[str],
) -> ProbabilityResult:
    frame = df.dropna(subset=["pct_change"]).copy()
    if "date" in frame.columns:
        frame = frame.sort_values(by="date")

    pdf_records: List[Dict[str, object]] = []
    pdd_records: List[Dict[str, object]] = []

    for key_values, group in frame.groupby(list(group_keys)):
        if isinstance(key_values, str) or not isinstance(key_values, Sequence):
            key_values = (key_values,)
        key_dict = {name: value for name, value in zip(group_keys, key_values)}
        label = "-".join(str(value) for value in key_values)
        seq = group["pct_change"].tolist()
        probabilities = _conditional_probability(seq)

        for direction in ("Up", "Down"):
            record = {**key_dict, "Label": label, "Direction": direction, "Probability": probabilities[direction]}
            pdf_records.append(record)
            pdd_records.append({**record, "SignedProbability": probabilities[direction] if direction == "Up" else -probabilities[direction]})

    pdf = pd.DataFrame(pdf_records)
    pdd = pd.DataFrame(pdd_records)

    pdf_path = output_root / f"{symbol}_{mode}_conditional_probability.csv"
    pdd_path = output_root / f"{symbol}_{mode}_conditional_probability_signed.csv"
    _save_csv(pdf, pdf_path)
    _save_csv(pdd, pdd_path)

    prob_series = pdd["SignedProbability"] if not pdd.empty else pd.Series(dtype=float)
    _plot_signed_bar(
        pdd,
        f"{symbol} {mode} conditional probability",
        output_root / f"{symbol}_{mode}_conditional_probability.png",
        x_col="Label",
        y_col="SignedProbability",
        direction_col="Direction",
        stats=_describe_series(prob_series),
    )

    return ProbabilityResult(pdf=pdf, pdd=pdd)


def prepare_daily_frame(daily_df: pd.DataFrame) -> pd.DataFrame:
    if daily_df is None:
        return pd.DataFrame()

    frame = daily_df.dropna(subset=["pct_change", "date"]).copy()
    frame["weekday"] = _weekday_name_index(frame["date"])
    frame = frame[frame["weekday"].isin(WEEKDAYS)]
    return frame


def prepare_hourly_frame(hourly_df: pd.DataFrame) -> pd.DataFrame:
    if hourly_df is None:
        return pd.DataFrame()

    frame = _localise_hourly(hourly_df).dropna(subset=["pct_change", "slot", "weekday"])
    frame["hour_label"] = frame.apply(_hour_label_from_row, axis=1)
    frame = frame[frame["weekday"].isin(WEEKDAYS)]
    return frame


def generate_probability_daily(symbol: str, daily_df: pd.DataFrame, output_root: Path) -> ProbabilityResult:
    frame = prepare_daily_frame(daily_df)
    if frame.empty:
        pdf_columns = ["weekday", "Label", "Direction", "Probability"]
        pdd_columns = pdf_columns + ["SignedProbability"]
        pdf = pd.DataFrame(columns=pdf_columns)
        pdd = pd.DataFrame(columns=pdd_columns)
        base = output_root / "daily"
        _save_csv(pdf, base / f"{symbol}_daily_conditional_probability.csv")
        _save_csv(pdd, base / f"{symbol}_daily_conditional_probability_signed.csv")
        prob_series = pdd["SignedProbability"] if not pdd.empty else pd.Series(dtype=float)
        _plot_signed_bar(
            pdd,
            f"{symbol} daily conditional probability",
            base / f"{symbol}_daily_conditional_probability.png",
            x_col="Label",
            y_col="SignedProbability",
            direction_col="Direction",
            stats=_describe_series(prob_series),
        )
        return ProbabilityResult(pdf=pdf, pdd=pdd)

    result = generate_conditional_probabilities(
        symbol,
        frame,
        output_root / "daily",
        mode="daily",
        group_keys=["weekday"],
    )
    return result


def generate_probability_hourly(symbol: str, hourly_df: pd.DataFrame, output_root: Path) -> ProbabilityResult:
    frame = prepare_hourly_frame(hourly_df)
    if frame.empty:
        pdf_columns = ["weekday", "hour_label", "Label", "Direction", "Probability"]
        pdd_columns = pdf_columns + ["SignedProbability"]
        pdf = pd.DataFrame(columns=pdf_columns)
        pdd = pd.DataFrame(columns=pdd_columns)
        base = output_root / "hourly"
        _save_csv(pdf, base / f"{symbol}_hourly_conditional_probability.csv")
        _save_csv(pdd, base / f"{symbol}_hourly_conditional_probability_signed.csv")
        prob_series = pdd["SignedProbability"] if not pdd.empty else pd.Series(dtype=float)
        _plot_signed_bar(
            pdd,
            f"{symbol} hourly conditional probability",
            base / f"{symbol}_hourly_conditional_probability.png",
            x_col="Label",
            y_col="SignedProbability",
            direction_col="Direction",
            stats=_describe_series(prob_series),
        )
        return ProbabilityResult(pdf=pdf, pdd=pdd)

    result = generate_conditional_probabilities(
        symbol,
        frame,
        output_root / "hourly",
        mode="hourly",
        group_keys=["weekday", "hour_label"],
    )
    return result


def generate_hourly_streaks(symbol: str, hourly_df: pd.DataFrame, output_root: Path) -> pd.DataFrame:
    frame = prepare_hourly_frame(hourly_df)
    records: List[Dict[str, object]] = []
    base_dir = output_root / "hourly" / "streaks" / "weekday"
    _ensure_directory(base_dir)
    columns = ["Weekday", "Hour", "Direction", "Streak Length", "Frequency", "Relative Frequency", "Total Runs"]

    for weekday in WEEKDAYS:
        weekday_frame = frame[frame["weekday"] == weekday] if not frame.empty else frame
        _ensure_directory(base_dir / weekday)

        for slot in range(1, HOURLY_SLOTS + 1):
            slot_frame = weekday_frame[weekday_frame["slot"] == slot] if not weekday_frame.empty else weekday_frame
            slot_frame = slot_frame.sort_values(by="date") if not slot_frame.empty else slot_frame

            if slot_frame is not None and not slot_frame.empty:
                label_candidates = slot_frame["hour_label"].dropna().mode()
                label = label_candidates.iloc[0] if not label_candidates.empty else _format_hour_label(int(slot), None)
            else:
                label = _format_hour_label(slot, None)

            if slot_frame is None or slot_frame.empty:
                streak_records: List[Dict[str, object]] = []
            else:
                streaks = _compute_streaks(slot_frame["pct_change"].tolist())
                counts: Dict[Tuple[str, int], int] = {}
                totals: Dict[str, int] = {"Up": 0, "Down": 0}
                for direction, length in streaks:
                    key = (direction, length)
                    counts[key] = counts.get(key, 0) + 1
                    totals[direction] += 1

                streak_records = []
                for (direction, length), frequency in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1])):
                    total_runs = totals[direction]
                    record = {
                        "Weekday": weekday,
                        "Hour": label,
                        "Direction": direction,
                        "Streak Length": int(length),
                        "Frequency": int(frequency),
                        "Relative Frequency": frequency / total_runs if total_runs else 0.0,
                        "Total Runs": int(total_runs),
                    }
                    records.append(record)
                    streak_records.append(record)

            frame_out = pd.DataFrame(streak_records, columns=columns)
            csv_path = base_dir / weekday / f"{symbol}_{weekday}_{label}_streaks.csv"
            plot_path = base_dir / weekday / f"{symbol}_{weekday}_{label}_streaks.png"
            _save_csv(frame_out, csv_path)
            freq_series = frame_out["Frequency"] if not frame_out.empty else pd.Series(dtype=float)
            _plot_signed_bar(
                frame_out,
                f"{symbol} {weekday} {label} hourly streaks",
                plot_path,
                x_col="Streak Length",
                y_col="Frequency",
                direction_col="Direction",
                stats=_describe_series(freq_series),
            )

    output = pd.DataFrame(records, columns=columns)
    _save_csv(output, output_root / "hourly" / f"{symbol}_hourly_streaks.csv")
    return output


def summarise_statistics(
    symbol: str,
    bundle: DatasetBundle,
    *,
    stats_root: Path,
    bucket: str | None = None,
) -> Dict[str, object]:
    base_root = stats_root
    if bucket:
        base_root = base_root / bucket.upper()

    output_root = base_root / symbol.upper()
    _ensure_directory(output_root)

    daily_summary = generate_daily_distributions(symbol, bundle.daily, output_root)
    hourly_summary = generate_hourly_distributions(symbol, bundle.hourly, output_root)

    weekday_streaks = generate_weekday_streaks(symbol, bundle.daily, output_root)
    continuous_daily = generate_continuous_streaks(symbol, prepare_daily_frame(bundle.daily), output_root / "daily" / "streaks", label="continuous")
    continuous_hourly = generate_continuous_streaks(symbol, prepare_hourly_frame(bundle.hourly), output_root / "hourly" / "streaks", label="continuous")

    daily_probability = generate_probability_daily(symbol, bundle.daily, output_root)
    hourly_probability = generate_probability_hourly(symbol, bundle.hourly, output_root)

    hourly_streaks = generate_hourly_streaks(symbol, bundle.hourly, output_root)

    return {
        "daily_summary_rows": int(daily_summary.shape[0]) if daily_summary is not None else 0,
        "hourly_summary_rows": int(hourly_summary.shape[0]) if hourly_summary is not None else 0,
        "weekday_streak_rows": int(weekday_streaks.shape[0]) if weekday_streaks is not None else 0,
        "continuous_daily_rows": int(continuous_daily.shape[0]) if continuous_daily is not None else 0,
        "continuous_hourly_rows": int(continuous_hourly.shape[0]) if continuous_hourly is not None else 0,
        "daily_probability_rows": int(daily_probability.pdf.shape[0]) if daily_probability.pdf is not None else 0,
        "hourly_probability_rows": int(hourly_probability.pdf.shape[0]) if hourly_probability.pdf is not None else 0,
        "hourly_streak_rows": int(hourly_streaks.shape[0]) if hourly_streaks is not None else 0,
    }


def process_symbol_path(symbol_dir: str, stats_root: str, bucket: str | None = None) -> Dict[str, object]:
    """Helper entrypoint for parallel workers.

    Accepts string paths (safe for multiprocessing on Windows), loads the
    datasets and runs summarise_statistics. Returns a serialisable dict with
    result metadata.
    """
    try:
        symbol_path = Path(symbol_dir)
        stats_root_path = Path(stats_root)
        symbol = symbol_path.name.upper()

        bundle = load_symbol_datasets(symbol_path)
        if bundle.daily is None and bundle.hourly is None:
            return {"symbol": symbol, "success": False, "reason": "no_datasets"}

        summary = summarise_statistics(symbol, bundle, stats_root=stats_root_path, bucket=bucket)
        return {"symbol": symbol, "success": True, "summary": summary}
    except Exception as exc:  # capture and return errors rather than crashing the worker
        return {"symbol": Path(symbol_dir).name.upper(), "success": False, "reason": "exception", "error": repr(exc)}
