import sqlite3
conn = sqlite3.connect("patient_variants.db")
cur = conn.cursor()
cur.execute("PRAGMA table_info(variants)")
cols = [r[1] for r in cur.fetchall()]
print("All columns:")
for c in cols:
    print(" ", repr(c))
conn.close()
