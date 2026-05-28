import sqlite3

conn = sqlite3.connect('genomic_variants.db')
cur = conn.cursor()

# Check all tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("=== TABLES ===")
for t in tables:
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    count = cur.fetchone()[0]
    print(f"  {t}: {count} rows")

# Check variant at chr1:10611
print("\n=== VARIANT AT chr1:10611 ===")
cur.execute("SELECT variant_id, variant_key, gene_symbol, ref_allele, alt_allele, rsid, hgvs_c, hgvs_p, intervar_classification FROM variants WHERE chromosome='1' AND start_pos=10611 LIMIT 5")
for r in cur.fetchall():
    print(r)

# Check InterVar classification distribution
print("\n=== INTERVAR CLASSIFICATION DISTRIBUTION ===")
cur.execute("""
SELECT
  CASE
    WHEN intervar_classification LIKE 'InterVar: Pathogenic%' THEN 'Pathogenic'
    WHEN intervar_classification LIKE 'InterVar: Likely pathogenic%' THEN 'Likely Pathogenic'
    WHEN intervar_classification LIKE 'InterVar: Uncertain%' THEN 'VUS'
    WHEN intervar_classification LIKE 'InterVar: Likely benign%' THEN 'Likely Benign'
    WHEN intervar_classification LIKE 'InterVar: Benign%' THEN 'Benign'
    ELSE 'Other'
  END as tier,
  COUNT(*) as count
FROM variants GROUP BY tier ORDER BY count DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]:,}")

conn.close()
