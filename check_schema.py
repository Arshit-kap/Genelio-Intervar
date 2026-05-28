import sqlite3
conn = sqlite3.connect('genomic_variants.db')
cur = conn.cursor()
for tbl in ['criterion_assessments', 'interpretation_evidence_lines', 'variant_interpretations', 'conditions']:
    cur.execute(f"PRAGMA table_info({tbl})")
    cols = cur.fetchall()
    print(f"\n{tbl}:")
    for c in cols:
        print(f"  {c[1]} {c[2]}")
conn.close()
