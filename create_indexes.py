"""Create database indexes one at a time, with progress reporting."""
import sqlite3, time

con = sqlite3.connect("genomic_variants.db", timeout=60)
con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA synchronous=NORMAL")

existing = {r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='variants'"
).fetchall()}
print("Existing indexes:", sorted(existing))

# Create most critical index first — gene_symbol for variant search
indexes = [
    ("idx_v_gene",   "CREATE INDEX IF NOT EXISTS idx_v_gene   ON variants(gene_symbol)"),
    ("idx_v_rsid",   "CREATE INDEX IF NOT EXISTS idx_v_rsid   ON variants(rs_id)"),
    ("idx_v_chrom",  "CREATE INDEX IF NOT EXISTS idx_v_chrom  ON variants(chrom, pos)"),
    ("idx_v_class",  "CREATE INDEX IF NOT EXISTS idx_v_class  ON variants(intervar_classification)"),
    ("idx_v_cadd",   "CREATE INDEX IF NOT EXISTS idx_v_cadd   ON variants(cadd_phred)"),
]

for name, sql in indexes:
    if name not in existing:
        print(f"Creating {name}...", end="", flush=True)
        t0 = time.time()
        con.execute(sql)
        con.commit()
        print(f" done in {time.time()-t0:.1f}s")
    else:
        print(f"Already exists: {name}")

con.close()
print("All indexes done.")
