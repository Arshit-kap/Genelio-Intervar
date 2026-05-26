import sqlite3
c = sqlite3.connect('/ephemeral/ubuntu/intervar/genomic_variants.db', timeout=10)
cur = c.cursor()
cur.execute('PRAGMA table_info(variants)')
cols = cur.fetchall()
print('ALL COLUMNS:')
for col in cols:
    print(f'  {col[0]:3}. {col[1]:35} {col[2]}')
c.close()
