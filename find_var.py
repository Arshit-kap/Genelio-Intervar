import sqlite3
conn = sqlite3.connect("patient_variants.db")
cur = conn.cursor()
cur.execute('SELECT Chr, Start, "Ref.Gene", "clinvar: Clinvar" FROM variants WHERE "clinvar: Clinvar" LIKE "clinvar: Pathogenic%" LIMIT 3')
for r in cur.fetchall():
    print(f"Chr{r[0]}:{r[1]}  gene={r[2]}  cv={r[3]}")
conn.close()
