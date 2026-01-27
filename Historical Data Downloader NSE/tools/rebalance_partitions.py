"""Utility to rebalance symbol folders across P1/P2/P3 buckets.

The Kite-based downloader originally placed every symbol inside ``P1``.
This helper reads ``Tickers/nse_symbols_all.csv`` to determine the canonical
symbol order and then moves each existing folder into the correct bucket:

* Symbols 1-1000 -> ``P1``
* Symbols 1001-2000 -> ``P2``
* Symbols 2001+ -> ``P3``

Run it any time the bucket layout changes or when new tickers are appended
and you want to keep Git-friendly folder sizes.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS_FILE = DEFAULT_ROOT / "Tickers" / "nse_symbols_all.csv"
PARTITIONS: Tuple[Tuple[int, Optional[int], str], ...] = (
    (1, 1000, "P1"),
    (1001, 2000, "P2"),
    (2001, None, "P3"),
)


def read_symbols(path: Path) -> List[str]:
    if not path.exists():
        raise SystemExit(f"Symbols file '{path}' not found. Run fetch_all_nse_symbols.py first.")

    symbols: List[str] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for raw_line in handle:
            value = raw_line.strip().lstrip("\ufeff")
            if not value or value.startswith("#"):
                continue
            symbol = value[:-3] if value.upper().endswith(".NS") else value
            symbols.append(symbol.upper())
    if not symbols:
        raise SystemExit(f"No symbols found inside '{path}'.")
    return symbols


def bucket_for_index(index: int) -> str:
    for start, end, bucket in PARTITIONS:
        if index >= start and (end is None or index <= end):
            return bucket
    return PARTITIONS[-1][2]


def locate_symbol_folder(root: Path, symbol: str) -> Optional[Path]:
    for _, _, bucket in PARTITIONS:
        candidate = root / bucket / symbol
        if candidate.exists():
            return candidate
    legacy = root / symbol
    if legacy.exists():
        return legacy
    return None


def move_symbol(symbol: str, symbol_index: int, root: Path, *, dry_run: bool, current: Optional[Path] = None) -> bool:
    existing = current or locate_symbol_folder(root, symbol)
    if existing is None:
        return False

    target_bucket = bucket_for_index(symbol_index)
    target = root / target_bucket / symbol
    if existing.resolve() == target.resolve():
        return False

    if target.exists():
        raise SystemExit(f"Refusing to overwrite existing folder '{target}'. Please investigate manually.")

    if dry_run:
        print(f"DRY RUN :: would move {symbol} -> {target_bucket}")
        return True

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(existing), str(target))
    print(f"Moved {symbol} -> {target_bucket}")
    return True


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebalance symbol folders across P1/P2/P3 buckets.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Repository root that contains P1/P2/P3 folders.")
    parser.add_argument("--symbols-file", type=Path, default=DEFAULT_SYMBOLS_FILE, help="CSV containing the canonical ticker order.")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N symbols (useful for smoke tests).")
    parser.add_argument("--dry-run", action="store_true", help="Print planned moves without touching the filesystem.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    symbols = read_symbols(args.symbols_file.resolve())
    if args.limit is not None and args.limit >= 0:
        symbols = symbols[: args.limit]

    root = args.root.resolve()
    moved = 0
    skipped_missing = 0

    for idx, symbol in enumerate(symbols, start=1):
        folder = locate_symbol_folder(root, symbol)
        if folder is None:
            skipped_missing += 1
            continue
        if move_symbol(symbol, idx, root, dry_run=args.dry_run, current=folder):
            moved += 1

    print(
        f"Rebalance complete: {moved} folder{'s' if moved != 1 else ''} moved, "
        f"{skipped_missing} symbol{'s' if skipped_missing != 1 else ''} missing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
