"""
scripts/fetch_data.py — Download a USAspending FY contracts archive and stream-filter
it to the analysis slice, without ever holding the full multi-GB CSV on disk.

Stdlib only (urllib, zipfile, csv) — runs on a bare macOS/Linux python3, no pip needed.

What it does:
  1. Asks the USAspending API for the current Award Data Archive file URL
     (file names carry a refresh date stamp, e.g. FY2023_All_Contracts_Full_20260608.zip,
     so the URL cannot be hard-coded).
  2. Downloads the zip to data/raw/ (resumable skip if already present).
  3. Streams the CSVs inside the zip and keeps only rows with:
        - naics_code starting with NAICS_PREFIX (default "33" — manufacturing)
        - federal_action_obligation >= MIN_AMOUNT (default $1,000)
     up to MAX_ROWS rows, writing the columns src/data_loader.py expects to
     data/raw/FY2023_contracts.csv.

Usage:
    python3 scripts/fetch_data.py            # FY2023, NAICS 33, 150K rows max
    python3 scripts/fetch_data.py --fy 2023 --naics 33 --max-rows 150000
    python3 scripts/fetch_data.py --keep-zip # don't delete the archive after filtering
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

API_URL = "https://api.usaspending.gov/api/v2/bulk_download/list_monthly_files/"

# Columns src/data_loader.py expects -> acceptable source-header fallbacks
# (USAspending has renamed headers across archive refreshes)
COLUMN_FALLBACKS = {
    "award_id_piid": ["award_id_piid"],
    "recipient_name": ["recipient_name", "recipient_name_raw", "vendor_name"],
    "recipient_uei": ["recipient_uei", "awardee_or_recipient_uei"],
    "naics_code": ["naics_code"],
    "naics_description": ["naics_description"],
    "action_date": ["action_date"],
    "period_of_performance_start_date": ["period_of_performance_start_date"],
    "period_of_performance_current_end_date": ["period_of_performance_current_end_date"],
    "federal_action_obligation": ["federal_action_obligation"],
    "base_and_all_options_value": ["base_and_all_options_value", "potential_total_value_of_award"],
    "awarding_agency_name": ["awarding_agency_name"],
    "awarding_sub_agency_name": ["awarding_sub_agency_name"],
    "place_of_performance_state_code": [
        "place_of_performance_state_code",
        "primary_place_of_performance_state_code",
        "pop_state_code",
    ],
}


def get_archive_url(fiscal_year: int) -> tuple[str, str]:
    """Ask the API for the current full-contracts archive file for a fiscal year."""
    body = json.dumps(
        {"agency": "all", "type": "contracts", "fiscal_year": fiscal_year}
    ).encode()
    req = urllib.request.Request(
        API_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    files = payload.get("monthly_files", [])
    full = [f for f in files if "Full" in f.get("file_name", "")] or files
    if not full:
        sys.exit(f"Unexpected API response:\n{json.dumps(payload, indent=2)[:2000]}")
    f = full[0]
    return f["file_name"], f["url"]


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 1e6:
        print(f"[fetch] Archive already present: {dest} — skipping download.")
        return
    print(f"[fetch] Downloading {url}")
    with urllib.request.urlopen(url, timeout=600) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(1 << 22):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done/1e9:.2f} / {total/1e9:.2f} GB", end="", flush=True)
        print()


def stream_filter(
    zip_path: Path, out_path: Path, naics_prefix: str, min_amount: float, max_rows: int
) -> int:
    kept = 0
    writer = None
    out = open(out_path, "w", newline="")
    with zipfile.ZipFile(zip_path) as zf:
        members = sorted(n for n in zf.namelist() if n.endswith(".csv"))
        print(f"[fetch] Archive contains {len(members)} CSV file(s)")
        for member in members:
            if kept >= max_rows:
                break
            print(f"[fetch] Scanning {member} (kept so far: {kept:,})")
            with zf.open(member) as fbin:
                ftxt = io.TextIOWrapper(fbin, encoding="utf-8", errors="replace")
                reader = csv.DictReader(ftxt)
                if writer is None:
                    # Resolve fallbacks against the actual header once
                    colmap = {}
                    missing = []
                    for want, options in COLUMN_FALLBACKS.items():
                        found = next((o for o in options if o in reader.fieldnames), None)
                        if found:
                            colmap[want] = found
                        else:
                            missing.append(want)
                    if missing:
                        print(f"[fetch] WARNING — columns not found, will be empty: {missing}")
                        print(f"[fetch] First 40 source headers: {reader.fieldnames[:40]}")
                    writer = csv.DictWriter(out, fieldnames=list(COLUMN_FALLBACKS))
                    writer.writeheader()
                for row in reader:
                    naics = row.get(colmap.get("naics_code", ""), "") or ""
                    if not naics.startswith(naics_prefix):
                        continue
                    try:
                        amount = float(row.get(colmap.get("federal_action_obligation", ""), "") or 0)
                    except ValueError:
                        continue
                    if amount < min_amount:
                        continue
                    writer.writerow({w: row.get(s, "") for w, s in colmap.items()})
                    kept += 1
                    if kept % 10000 == 0:
                        print(f"\r  kept {kept:,} rows", end="", flush=True)
                    if kept >= max_rows:
                        break
            print()
    out.close()
    return kept


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch + filter USAspending contracts archive")
    ap.add_argument("--fy", type=int, default=2023)
    ap.add_argument("--naics", type=str, default="33", help="NAICS prefix filter")
    ap.add_argument("--min-amount", type=float, default=1000.0)
    ap.add_argument("--max-rows", type=int, default=150_000)
    ap.add_argument("--keep-zip", action="store_true")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    name, url = get_archive_url(args.fy)
    print(f"[fetch] Current archive: {name}")
    zip_path = RAW_DIR / name
    download(url, zip_path)

    out_path = RAW_DIR / f"FY{args.fy}_contracts.csv"
    kept = stream_filter(zip_path, out_path, args.naics, args.min_amount, args.max_rows)
    print(f"[fetch] Wrote {kept:,} rows -> {out_path} "
          f"({out_path.stat().st_size/1e6:.1f} MB)")

    if not args.keep_zip:
        zip_path.unlink()
        print(f"[fetch] Deleted archive {zip_path.name} to free disk.")


if __name__ == "__main__":
    main()
