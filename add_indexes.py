"""Add missing indexes to improve query performance."""
import sqlite3, time

con = sqlite3.connect("genomic_variants.db", timeout=60)
cur = con.cursor()

existing = {r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='variants'"
).fetchall()}
print("Existing indexes:", sorted(existing))

indexes = [
    ("idx_variants_intervar_class", "CREATE INDEX IF NOT EXISTS idx_variants_intervar_class ON variants(intervar_classification)"),
    ("idx_variants_gene_symbol",    "CREATE INDEX IF NOT EXISTS idx_variants_gene_symbol ON variants(gene_symbol)"),
    ("idx_variants_chrom_pos",      "CREATE INDEX IF NOT EXISTS idx_variants_chrom_pos ON variants(chromosome, start_pos)"),
    ("idx_variants_cadd_phred",     "CREATE INDEX IF NOT EXISTS idx_variants_cadd_phred ON variants(cadd_phred)"),
    ("idx_variants_clinvar_sig",    "CREATE INDEX IF NOT EXISTS idx_variants_clinvar_sig ON variants(clinvar_significance)"),
    ("idx_variants_gnomad_af",      "CREATE INDEX IF NOT EXISTS idx_variants_gnomad_af ON variants(gnomad_af_all)"),
    ("idx_variants_exonic_func",    "CREATE INDEX IF NOT EXISTS idx_variants_exonic_func ON variants(exonic_func)"),
    ("idx_variants_rsid",           "CREATE INDEX IF NOT EXISTS idx_variants_rsid ON variants(rsid)"),
]

for name, sql in indexes:
    if name not in existing:
        print(f"Creating {name} ...")
        t0 = time.time()
        cur.execute(sql)
        con.commit()
        print(f"  done in {time.time()-t0:.1f}s")
    else:
        print(f"Already exists: {name}")

con.close()
print("All indexes ready.")
