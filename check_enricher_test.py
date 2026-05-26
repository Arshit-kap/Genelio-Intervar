import sys
sys.path.insert(0, "/home/ubuntu/intervar")
import sqlite3

con = sqlite3.connect("/home/ubuntu/intervar/patient_variants.db")
cur = con.cursor()

sql = 'SELECT Orpha, OMIM, Phenotype_MIM FROM variants WHERE "Ref.Gene" = ? AND Orpha IS NOT NULL AND Orpha != "" LIMIT 1'
for gene in ["IDUA", "PADI3", "GHRL"]:
    cur.execute(sql, (gene,))
    row = cur.fetchone()
    if row:
        print(gene, "-> Orpha:", str(row[0])[:100], "| OMIM:", row[1])
    else:
        print(gene, "-> No Orpha data in DB")
con.close()

# Test HPO API
from app.ai.gene_enricher import _fetch_hpo_gene_diseases
for gene in ["IDUA", "PADI3"]:
    diseases = _fetch_hpo_gene_diseases(gene)
    print(gene, "HPO:", [d["disease"] for d in diseases[:2]])

# Test Orpha parser
from app.ai.gene_enricher import _parse_orpha_column
sample = "319563|MSMD due to complete ISG15 deficiency|<1 / 1 000 000|Autosomal recessive|Childhood|616126 ~"
print("Orpha parse:", _parse_orpha_column(sample))
