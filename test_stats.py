"""Quick test of statistics queries."""
import sqlite3, time

con = sqlite3.connect("genomic_variants.db", timeout=15)
con.execute("PRAGMA journal_mode=WAL")

print("Testing MAX(variant_id)...")
t0 = time.time()
row = con.execute("SELECT MAX(variant_id) FROM variants").fetchone()
print(f"  max variant_id: {row[0]:,} ({time.time()-t0:.3f}s)")

t0 = time.time()
row2 = con.execute("SELECT MAX(gene_id) FROM genes").fetchone()
print(f"  max gene_id: {row2[0]:,} ({time.time()-t0:.3f}s)")

print("Testing prefix LIKE without index...")
t0 = time.time()
n = con.execute("SELECT COUNT(*) FROM variants WHERE intervar_classification LIKE 'InterVar: Benign %'").fetchone()[0]
print(f"  Benign count: {n:,} ({time.time()-t0:.2f}s)")

t0 = time.time()
n2 = con.execute("SELECT COUNT(*) FROM variants WHERE intervar_classification LIKE 'InterVar: Uncertain%'").fetchone()[0]
print(f"  VUS count: {n2:,} ({time.time()-t0:.2f}s)")

con.close()
print("Done.")
