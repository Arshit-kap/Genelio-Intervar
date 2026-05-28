import sqlite3
conn = sqlite3.connect('patient_variants.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute('SELECT COUNT(*) FROM variants WHERE "Ref.Gene" LIKE "%BRCA1%"')
print("BRCA1 variants:", cur.fetchone()[0])

cur.execute('SELECT "clinvar: Clinvar" FROM variants WHERE "Ref.Gene" LIKE "%BRCA1%" LIMIT 5')
for r in cur.fetchall():
    print("  clinvar value:", repr(r[0]))

cur.execute('SELECT COUNT(*) FROM variants WHERE "Ref.Gene" LIKE "%BRCA1%" AND "clinvar: Clinvar" LIKE "%athogenic%"')
print("BRCA1 pathogenic:", cur.fetchone()[0])

cur.execute('SELECT COUNT(*) FROM variants WHERE "clinvar: Clinvar" LIKE "clinvar: Pathogenic%"')
print("All pathogenic:", cur.fetchone()[0])
conn.close()
