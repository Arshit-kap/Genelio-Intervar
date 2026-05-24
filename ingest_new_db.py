"""
InterVar Patient Variant File → SQLite Database
================================================
Ingests a 34-column InterVar-annotated tab-separated txt file into SQLite.
  - '#Chr' header → stored as 'Chr'
  - Leading/trailing spaces stripped from all column headers
  - '.' values in numeric columns stored as NULL
  - Creates all required indexes for fast querying
  - Uses chunked reads (10,000 rows/batch) with executemany

Usage:
  python ingest_new_db.py <input.txt> [output.db]
  Defaults: output = patient_variants.db in same directory as input
"""
import sqlite3
import sys
import os
import time

CHUNK = 10_000

NUMERIC_COLS = {
    "Freq_gnomAD_genome_ALL", "Freq_esp6500siv2_all", "Freq_1000g2015aug_all",
    "CADD_raw", "CADD_phred", "SIFT_score", "GERP++_RS",
    "phyloP46way_placental", "dbscSNV_ADA_SCORE", "dbscSNV_RF_SCORE",
    "MetaSVM_score",
}

INTEGER_COLS = {"Start", "End"}

# DB column name → CREATE TABLE definition (with SQL type)
# Column order matches the 34-column InterVar output spec
DB_COLS = [
    ("Chr",                              "TEXT"),
    ("Start",                            "INTEGER"),
    ("End",                              "INTEGER"),
    ("Ref",                              "TEXT"),
    ("Alt",                              "TEXT"),
    ("Ref.Gene",                         "TEXT"),
    ("Func.refGene",                     "TEXT"),
    ("ExonicFunc.refGene",               "TEXT"),
    ("Gene.ensGene",                     "TEXT"),
    ("avsnp147",                         "TEXT"),
    ("AAChange.ensGene",                 "TEXT"),
    ("AAChange.refGene",                 "TEXT"),
    ("clinvar: Clinvar",                 "TEXT"),
    ("InterVar: InterVar and Evidence",  "TEXT"),
    ("Freq_gnomAD_genome_ALL",           "REAL"),
    ("Freq_esp6500siv2_all",             "REAL"),
    ("Freq_1000g2015aug_all",            "REAL"),
    ("CADD_raw",                         "REAL"),
    ("CADD_phred",                       "REAL"),
    ("SIFT_score",                       "REAL"),
    ("GERP++_RS",                        "REAL"),
    ("phyloP46way_placental",            "REAL"),
    ("dbscSNV_ADA_SCORE",               "REAL"),
    ("dbscSNV_RF_SCORE",                "REAL"),
    ("Interpro_domain",                  "TEXT"),
    ("AAChange.knownGene",               "TEXT"),
    ("rmsk",                             "TEXT"),
    ("MetaSVM_score",                    "REAL"),
    ("Freq_gnomAD_genome_POPs",          "TEXT"),
    ("OMIM",                             "TEXT"),
    ("Phenotype_MIM",                    "TEXT"),
    ("OrphaNumber",                      "TEXT"),
    ("Orpha",                            "TEXT"),
    ("Otherinfo",                        "TEXT"),
]

# Columns to index (quoted where needed)
INDEXES = [
    "Chr",
    "Start",
    '"Ref.Gene"',
    '"Gene.ensGene"',
    "avsnp147",
    '"Func.refGene"',
    '"ExonicFunc.refGene"',
    '"InterVar: InterVar and Evidence"',
    '"clinvar: Clinvar"',
    "Otherinfo",
    "CADD_phred",
    "Freq_gnomAD_genome_ALL",
]

# Map file header → DB column name (handles #Chr, spaces, etc.)
def _normalise_header(raw: str) -> str:
    h = raw.strip()
    if h == "#Chr":
        return "Chr"
    # Strip leading/trailing spaces from column names with spaces
    return h


def _q(col: str) -> str:
    """Quote column name if it contains dots, spaces, +, :, #."""
    if any(c in col for c in (".", " ", "+", ":", "#")):
        return f'"{col}"'
    return col


def create_db(db_path: str) -> sqlite3.Connection:
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"Removed existing DB: {db_path}")

    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit for PRAGMA
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64 MB cache

    col_defs = ", ".join(
        f"{_q(name)} {dtype}" for name, dtype in DB_COLS
    )
    conn.execute(f"CREATE TABLE IF NOT EXISTS variants ({col_defs})")

    # Create indexes
    for col in INDEXES:
        # col may already be quoted (e.g., '"Ref.Gene"') or bare
        bare = col.strip('"')
        idx_name = f"idx_{bare.replace('.', '_').replace(' ', '_').replace(':', '_').replace('+', '_')}"
        conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON variants ({col})")

    conn.isolation_level = ""  # restore default for transactions
    return conn


def coerce(value: str, col: str):
    """Convert string value to appropriate Python type for SQLite."""
    if value in (".", "", "NA", "N/A", "nan", "None"):
        return None
    if col in NUMERIC_COLS:
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    if col in INTEGER_COLS:
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    return value


def ingest(txt_path: str, db_path: str):
    print(f"Source : {txt_path}")
    print(f"Target : {db_path}")
    print()

    conn = create_db(db_path)
    db_col_names = [name for name, _ in DB_COLS]
    placeholders = ", ".join("?" * len(DB_COLS))
    insert_sql = (
        f"INSERT INTO variants ({', '.join(_q(c) for c in db_col_names)}) "
        f"VALUES ({placeholders})"
    )

    t0 = time.time()
    total = 0
    skipped = 0
    batch = []

    with open(txt_path, "r", encoding="utf-8", errors="replace") as fh:
        # Parse header
        raw_headers = fh.readline().rstrip("\n").split("\t")
        file_cols = [_normalise_header(h) for h in raw_headers]

        # Build mapping: file column index → db column name
        col_map = []
        for db_col in db_col_names:
            try:
                idx = file_cols.index(db_col)
                col_map.append(idx)
            except ValueError:
                print(f"  WARNING: DB column '{db_col}' not found in file. Will store NULL.")
                col_map.append(None)

        print(f"Columns mapped: {sum(1 for i in col_map if i is not None)}/{len(db_col_names)}")
        print("Ingesting...")

        conn.execute("BEGIN")
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            try:
                row = []
                for db_idx, file_idx in enumerate(col_map):
                    if file_idx is None or file_idx >= len(parts):
                        row.append(None)
                    else:
                        col_name = db_col_names[db_idx]
                        row.append(coerce(parts[file_idx], col_name))
                batch.append(row)
            except Exception:
                skipped += 1
                continue

            if len(batch) >= CHUNK:
                conn.executemany(insert_sql, batch)
                total += len(batch)
                batch.clear()
                if total % 100_000 == 0:
                    elapsed = time.time() - t0
                    print(f"  {total:,} rows  ({elapsed:.0f}s)")

        # Flush remainder
        if batch:
            conn.executemany(insert_sql, batch)
            total += len(batch)

        conn.execute("COMMIT")

    elapsed = time.time() - t0
    print(f"\nDone. {total:,} rows inserted, {skipped} skipped. Time: {elapsed:.1f}s")

    # Verify
    row = conn.execute("SELECT COUNT(*) FROM variants").fetchone()
    print(f"DB row count: {row[0]:,}")

    db_size = os.path.getsize(db_path)
    print(f"DB size: {db_size / 1024 / 1024:.1f} MB")
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ingest_new_db.py <input.txt> [output.db]")
        sys.exit(1)
    txt = sys.argv[1]
    db = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(txt), "patient_variants.db")
    ingest(txt, db)
