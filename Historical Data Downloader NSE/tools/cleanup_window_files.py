from pathlib import Path

def main():
    base = Path(__file__).resolve().parents[1]  # Historical Data Downloader NSE
    pattern = "**/*_window_*.csv"
    files = list(base.glob(pattern))
    if not files:
        print("No window CSV files found.")
        return
    print(f"Found {len(files)} window CSV files. Deleting...")
    deleted = 0
    for p in files:
        try:
            p.unlink()
            deleted += 1
        except Exception as e:
            print(f"Failed to delete {p}: {e}")
    print(f"Deleted {deleted} files.")

if __name__ == '__main__':
    main()
