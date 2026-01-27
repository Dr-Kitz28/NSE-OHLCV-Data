from pathlib import Path
import csv

def find_matches(base_dir: Path, date_token: str = '2026-01-23'):
    pattern = '**/*_hourly.csv'
    results = []
    for p in base_dir.glob(pattern):
        try:
            with p.open('r', encoding='utf-8', errors='ignore') as f:
                count = 0
                first_line = ''
                for line in f:
                    if date_token in line:
                        count += 1
                        if not first_line:
                            first_line = line.strip()
                if count > 0:
                    results.append((str(p.relative_to(base_dir)), count, first_line))
        except Exception:
            continue
    return results

def write_report(base_dir: Path, rows):
    out_dir = base_dir / 'reports'
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / 'hourly_20260123_matches.csv'
    with out_file.open('w', newline='', encoding='utf-8') as csvf:
        writer = csv.writer(csvf)
        writer.writerow(['file', 'match_count', 'first_matching_line'])
        for r in rows:
            writer.writerow(r)
    return out_file

def main():
    base = Path(__file__).resolve().parents[1]  # Historical Data Downloader NSE
    rows = find_matches(base, '2026-01-23')
    if not rows:
        print('No hourly files contain 2026-01-23 rows.')
        return
    out = write_report(base, rows)
    print(f'Found {len(rows)} hourly files with 2026-01-23 rows.')
    print(f'Report written to: {out}')

if __name__ == '__main__':
    main()
