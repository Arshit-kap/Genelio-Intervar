import sqlite3
conn = sqlite3.connect('genomic_variants.db')
c = conn.cursor()

print('=== CFTR exonic_func ===')
c.execute("SELECT exonic_func, COUNT(*) FROM variants WHERE gene_symbol = 'CFTR' GROUP BY exonic_func ORDER BY COUNT(*) DESC LIMIT 10")
for row in c.fetchall():
    print(row)

print('\n=== Indexes ===')
c.execute("SELECT name, sql FROM sqlite_master WHERE type='index' ORDER BY name")
for row in c.fetchall():
    print(row[0], '|', (row[1] or '')[:100])

print('\n=== ClinVar Pathogenic + gnomAD > 1% count (fast check) ===')
c.execute("SELECT COUNT(*) FROM variants WHERE clinvar_significance LIKE 'Pathogenic%' AND gnomad_af_all > 0.01")
print(c.fetchone())

conn.close()
