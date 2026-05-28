import sqlite3
conn = sqlite3.connect("patient_variants.db")
cur = conn.cursor()
cur.execute("SELECT Chr, Start, \"Ref.Gene\" FROM variants WHERE Chr='1' AND Start=17270928")
print("exact match:", cur.fetchall())
cur.execute("SELECT Chr, Start, \"Ref.Gene\" FROM variants WHERE Chr='1' AND Start BETWEEN 17270920 AND 17270935")
print("range match:", cur.fetchall())
conn.close()
