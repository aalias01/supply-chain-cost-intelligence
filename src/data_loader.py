"""
src/data_loader.py — Download USAspending.gov federal procurement data and load into DuckDB.

USAspending.gov is the US government's open data portal for federal spending.
Every federal contract is published here: vendor name, NAICS code, dollar amount,
period of performance, agency, location.

This is real procurement data — structurally identical to private-sector supplier
intelligence (same cost / vendor / category schema as a corporate BOM).

Usage:
    python -m src.data_loader          # download + load full slice
    python -m src.data_loader --sample # save 500-row sample only (for demo)

Or from a notebook:
    from src.data_loader import load_db, get_awards_summary
    conn = load_db()
    df = conn.execute("SELECT * FROM federal_awards LIMIT 100").df()
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve paths relative to the project root (src/ → project root) so the
# loader, notebooks (run from notebooks/), and the Quarto report (rendered
# from report/) all see the same absolute paths.
PROJECT_ROOT  = Path(__file__).resolve().parent.parent
RAW_DIR       = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SAMPLE_DIR    = PROJECT_ROOT / "data" / "sample"
DB_PATH       = PROCESSED_DIR / "supply_chain.duckdb"

# USAspending Award Data Archive — fiscal year bulk download
# Filter to a manageable scope: one agency × one fiscal year
# Docs: https://www.usaspending.gov/download_center/award_data_archive
USASPENDING_FY         = 2023
USASPENDING_AWARD_TYPE = "contracts"   # contracts | grants | loans | direct_payments

# Primary columns we need from the raw CSV (USAspending has 200+ columns)
AWARD_COLUMNS = {
    "award_id_piid":                  str,    # contract ID
    "recipient_name":                 str,    # vendor name
    "recipient_uei":                  str,    # unique entity ID
    "naics_code":                     str,    # NAICS category (6-digit)
    "naics_description":              str,
    "action_date":                    str,    # date of award action
    "period_of_performance_start_date": str,
    "period_of_performance_current_end_date": str,
    "federal_action_obligation":      float,  # award amount ($)
    "base_and_all_options_value":     float,  # total contract value
    "awarding_agency_name":           str,
    "awarding_sub_agency_name":       str,
    "place_of_performance_state_code": str,
}


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def download_usaspending_csv(
    fiscal_year: int = USASPENDING_FY,
    award_type: str = USASPENDING_AWARD_TYPE,
    dest_dir: Path = RAW_DIR,
    force: bool = False,
) -> Path:
    """
    Download + filter USAspending bulk data for a given fiscal year.

    Delegates to scripts/fetch_data.py, which resolves the current Award Data
    Archive URL via the USAspending API (archive file names carry a refresh
    date stamp, so they cannot be hard-coded), streams the multi-GB zip, and
    keeps only the analysis slice.

    Returns:
        Path to the filtered CSV file
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    csv_path = dest_dir / f"FY{fiscal_year}_{award_type}.csv"

    if csv_path.exists() and not force:
        print(f"[data_loader] Already exists: {csv_path} — skipping download. Use force=True to re-download.")
        return csv_path

    import subprocess
    import sys as _sys
    script = PROJECT_ROOT / "scripts" / "fetch_data.py"
    print(f"[data_loader] Delegating download to {script} ...")
    subprocess.run([_sys.executable, str(script), "--fy", str(fiscal_year)], check=True)
    if not csv_path.exists():
        raise FileNotFoundError(f"fetch_data.py finished but {csv_path} not found.")
    return csv_path


# ---------------------------------------------------------------------------
# DuckDB schema setup
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS federal_awards (
    award_id_piid           VARCHAR,
    recipient_name          VARCHAR,
    recipient_uei           VARCHAR,
    naics_code              VARCHAR,
    naics_description       VARCHAR,
    action_date             DATE,
    perf_start_date         DATE,
    perf_end_date           DATE,
    action_amount           DOUBLE,    -- federal_action_obligation
    total_value             DOUBLE,    -- base_and_all_options_value
    agency_name             VARCHAR,
    sub_agency_name         VARCHAR,
    state_code              VARCHAR,

    -- Derived during load
    lead_time_days          INTEGER,   -- perf_end_date - action_date
    fy                      INTEGER    -- fiscal year
);

CREATE INDEX IF NOT EXISTS idx_naics   ON federal_awards (naics_code);
CREATE INDEX IF NOT EXISTS idx_vendor  ON federal_awards (recipient_name);
CREATE INDEX IF NOT EXISTS idx_date    ON federal_awards (action_date);
"""


def load_db(
    csv_path: Optional[Path] = None,
    db_path: Path = DB_PATH,
    fiscal_year: int = USASPENDING_FY,
    naics_filter: Optional[str] = None,
    min_amount: float = 1000.0,
    limit: Optional[int] = None,
) -> duckdb.DuckDBPyConnection:
    """
    Load federal awards CSV into DuckDB and return a connection.

    If the database already exists and has data, skips loading and returns
    the existing connection.

    Args:
        csv_path: path to USAspending CSV (download first with download_usaspending_csv)
        db_path: where to persist the DuckDB file
        fiscal_year: used for the `fy` derived column
        naics_filter: optional 2-digit NAICS prefix to filter (e.g. "33" for manufacturing)
        min_amount: exclude awards below this dollar value (removes micro-purchases)
        limit: optional row limit for development (None = load all)

    Returns:
        DuckDB connection ready for SQL queries
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    conn.execute(SCHEMA_SQL)

    # Check if already loaded
    existing = conn.execute("SELECT COUNT(*) FROM federal_awards").fetchone()[0]
    if existing > 0:
        print(f"[data_loader] DuckDB already has {existing:,} rows — skipping load.")
        return conn

    if csv_path is None:
        csv_path = RAW_DIR / f"FY{fiscal_year}_{USASPENDING_AWARD_TYPE}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"CSV not found at {csv_path}. "
            f"Run: python -m src.data_loader --download first."
        )

    print(f"[data_loader] Loading {csv_path} into DuckDB...")

    # Read with DuckDB's fast CSV reader
    reader_sql = f"""
        SELECT
            "award_id_piid"                             AS award_id_piid,
            "recipient_name"                            AS recipient_name,
            "recipient_uei"                             AS recipient_uei,
            CAST("naics_code" AS VARCHAR)               AS naics_code,
            "naics_description"                         AS naics_description,
            TRY_CAST("action_date" AS DATE)             AS action_date,
            TRY_CAST("period_of_performance_start_date" AS DATE) AS perf_start_date,
            TRY_CAST("period_of_performance_current_end_date" AS DATE) AS perf_end_date,
            TRY_CAST("federal_action_obligation" AS DOUBLE) AS action_amount,
            TRY_CAST("base_and_all_options_value" AS DOUBLE) AS total_value,
            "awarding_agency_name"                      AS agency_name,
            "awarding_sub_agency_name"                  AS sub_agency_name,
            "place_of_performance_state_code"           AS state_code,
            DATEDIFF('day',
                TRY_CAST("action_date" AS DATE),
                TRY_CAST("period_of_performance_current_end_date" AS DATE)
            )                                           AS lead_time_days,
            {fiscal_year}                               AS fy
        FROM read_csv_auto('{csv_path}', ignore_errors=True)
        WHERE TRY_CAST("federal_action_obligation" AS DOUBLE) >= {min_amount}
        {"AND CAST(naics_code AS VARCHAR) LIKE '" + naics_filter + "%'" if naics_filter else ""}
        {"LIMIT " + str(limit) if limit else ""}
    """

    conn.execute(f"INSERT INTO federal_awards {reader_sql}")
    n = conn.execute("SELECT COUNT(*) FROM federal_awards").fetchone()[0]
    print(f"[data_loader] Loaded {n:,} rows into federal_awards")
    return conn


# ---------------------------------------------------------------------------
# Convenience query functions
# ---------------------------------------------------------------------------

def get_awards_summary(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Quick summary of what's in the database."""
    return conn.execute("""
        SELECT
            COUNT(*)                                AS total_awards,
            COUNT(DISTINCT recipient_name)          AS unique_vendors,
            COUNT(DISTINCT naics_code)              AS unique_naics,
            SUM(action_amount) / 1e9                AS total_spend_billions,
            MIN(action_date)                        AS earliest_date,
            MAX(action_date)                        AS latest_date,
            AVG(lead_time_days)                     AS avg_lead_time_days
        FROM federal_awards
    """).df()


def get_top_naics(conn: duckdb.DuckDBPyConnection, top_n: int = 20) -> pd.DataFrame:
    """Top NAICS categories by total spend."""
    return conn.execute(f"""
        SELECT
            naics_code,
            naics_description,
            COUNT(*)                AS award_count,
            SUM(action_amount) / 1e6 AS spend_millions,
            COUNT(DISTINCT recipient_name) AS vendor_count
        FROM federal_awards
        GROUP BY naics_code, naics_description
        ORDER BY spend_millions DESC
        LIMIT {top_n}
    """).df()


def save_sample(
    conn: duckdb.DuckDBPyConnection,
    n: int = 500,
    dest: Path = SAMPLE_DIR / "awards_sample.csv",
) -> None:
    """Save a small sample to data/sample/ for repo reproducibility."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    df = conn.execute(f"""
        SELECT * FROM federal_awards
        ORDER BY RANDOM()
        LIMIT {n}
    """).df()
    df.to_csv(dest, index=False)
    print(f"[data_loader] Sample saved: {dest} ({len(df)} rows)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="USAspending data loader")
    parser.add_argument("--download", action="store_true", help="Download raw CSV from USAspending")
    parser.add_argument("--load",     action="store_true", help="Load CSV into DuckDB")
    parser.add_argument("--sample",   action="store_true", help="Save 500-row sample only")
    parser.add_argument("--fy",       type=int, default=USASPENDING_FY)
    parser.add_argument("--naics",    type=str, default=None, help="Filter NAICS prefix (e.g. '33')")
    parser.add_argument("--limit",    type=int, default=None, help="Row limit for dev")
    args = parser.parse_args()

    if args.download or (not args.sample):
        csv_path = download_usaspending_csv(fiscal_year=args.fy)
    else:
        csv_path = RAW_DIR / f"FY{args.fy}_{USASPENDING_AWARD_TYPE}.csv"

    if args.load or args.sample or (not args.download):
        conn = load_db(csv_path=csv_path, fiscal_year=args.fy,
                       naics_filter=args.naics, limit=args.limit)
        print(get_awards_summary(conn))
        if args.sample:
            save_sample(conn)
