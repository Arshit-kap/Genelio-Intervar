import sqlite3, time

conn = sqlite3.connect('genomic_variants.db')
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")
cur = conn.cursor()

tests = [
    ("Q8: Top 5 genes pathogenic",
     "SELECT gene_symbol, COUNT(*) AS pathogenic_count FROM variants WHERE intervar_classification LIKE 'InterVar: Pathogenic%' AND gene_symbol IS NOT NULL GROUP BY gene_symbol ORDER BY pathogenic_count DESC LIMIT 5"),

    ("Q9: Avg CADD stopgain vs synonymous",
     "SELECT exonic_func, ROUND(AVG(cadd_phred),2) AS avg_cadd, COUNT(*) AS count FROM variants WHERE cadd_phred IS NOT NULL AND exonic_func IN ('stopgain','synonymous SNV') GROUP BY exonic_func ORDER BY avg_cadd DESC LIMIT 20"),

    ("Q5: ClinVar Pathogenic + gnomAD > 1%",
     "SELECT variant_key, gene_symbol, clinvar_significance, gnomad_af_all FROM variants WHERE clinvar_significance LIKE 'Pathogenic%' AND gnomad_af_all > 0.01 LIMIT 100"),
]

for name, sql in tests:
    t0 = time.time()
    cur.execute(sql)
    rows = cur.fetchall()
    elapsed = time.time() - t0
    print(f"\n{name}")
    print(f"  Time: {elapsed:.2f}s  Rows: {len(rows)}")
    for r in rows[:3]:
        print(f"  {r}")

conn.close()
