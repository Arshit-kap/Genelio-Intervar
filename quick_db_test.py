"""Quick DB inspection."""
import sqlite3, time

con = sqlite3.connect("genomic_variants.db", timeout=10)
con.execute("PRAGMA journal_mode=WAL")

# Show columns
cols = [r[1] for r in con.execute("PRAGMA table_info(variants)").fetchall()]
print("Columns:", cols[:20])

# Test gene search
t0 = time.time()
rows = con.execute("SELECT variant_id, gene_symbol, variant_key FROM variants WHERE gene_symbol = 'BRCA1' LIMIT 3").fetchall()
print(f"BRCA1 query: {len(rows)} rows in {time.time()-t0:.3f}s")
for r in rows:
    print(" ", r)

con.close()
print("Done.")
