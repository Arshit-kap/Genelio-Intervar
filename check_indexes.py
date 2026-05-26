import sqlite3

conn = sqlite3.connect('genomic_variants.db')
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")
rows = cur.fetchall()
print("=== EXISTING INDEXES ===")
for r in rows:
    print(" ", r[0])
print("Total:", len(rows))

# Check columns that need indexes
print("\n=== CHECKING KEY COLUMNS ===")
cur.execute("PRAGMA table_info(variants)")
cols = [r[1] for r in cur.fetchall()]
print("Columns:", cols[:30])

conn.close()
