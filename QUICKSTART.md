# Quick Start Guide - Genomic Variant Database

## One-Minute Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Initialize Database

```bash
python scripts/setup_database.py
```

You should see:
```
============================================================
GENOMIC VARIANT DATABASE SETUP
============================================================
✓ Tables created successfully
✓ ACMG rules loaded successfully
✓ Column dictionary loaded successfully
✓ Source versions loaded successfully
============================================================
✓ DATABASE SETUP COMPLETE
============================================================
```

### 3. Load Your Data

```bash
python scripts/load_intervar.py path/to/your/intervar_file.tsv
```

Monitor progress in the logs. The pipeline will:
- Validate each row
- Parse frequencies and ACMG evidence
- Insert in batches of 1000
- Log any errors to `ingestion.log`

### 4. Verify Data

```bash
python scripts/verify_database.py
```

This shows:
- Total variants loaded
- Genes and chromosomes covered
- Data quality metrics
- Sample query results

## Quick Test

Try loading sample data:

```bash
# Create a small test file
echo -e "Chr\tStart\tEnd\tRef\tAlt\tGene.refGene\tExonicFunc.refGene\tFunc.refGene\tclinvar: Clinvar\tInterVar\tFreq_gnomAD_genome_POPs\tCADD_phred\tdbSNP147" > test.tsv
echo -e "1\t100000\t100000\tA\tT\tGENE1\tmissense\texonic\tpathogenic\tpathogenic(PVS1,PM2)\tAF=0.0001\t25.3\trs123456" >> test.tsv

# Load it
python scripts/load_intervar.py test.tsv

# Check results
python scripts/verify_database.py
```

## File Format Requirements

Your InterVar file should have these columns:

**Required:**
- Chr - Chromosome
- Start - Position start
- End - Position end  
- Ref - Reference allele
- Alt - Alternate allele

**Important (for parsing):**
- Gene.refGene - Gene symbol
- ExonicFunc.refGene - Exonic functional consequence
- Func.refGene - Functional region
- clinvar: Clinvar - ClinVar significance
- InterVar - InterVar classification
- Freq_gnomAD_genome_POPs - Population frequencies
- CADD_phred - CADD score
- SIFT_score - SIFT prediction
- MetaSVM_score - MetaSVM prediction
- dbSNP147 - rsID

Delimiter is auto-detected (tab or comma).

## Typical Workflow

```bash
# Week 1: Setup
pip install -r requirements.txt
python scripts/setup_database.py

# Week 1: Load data
python scripts/load_intervar.py gnomad.tsv
python scripts/load_intervar.py clinvar.tsv
python scripts/load_intervar.py custom_data.tsv

# Verify
python scripts/verify_database.py

# Week 2+: Query and analyze
sqlite3 genomic_variants.db < scripts/sample_queries.sql

# Or use Python
from app.database import SessionLocal
from app.models import Variant

db = SessionLocal()
variants = db.query(Variant).filter(
    Variant.clinvar_significance.like('%pathogenic%')
).limit(10).all()

for v in variants:
    print(f"{v.variant_key} - {v.gene_symbol} - {v.clinvar_significance}")
```

## Common Tasks

### Find pathogenic variants in a gene

```python
from app.database import SessionLocal
from app.models import Variant

db = SessionLocal()
variants = db.query(Variant).filter(
    (Variant.gene_symbol == 'BRCA1') &
    (Variant.clinvar_significance.like('%pathogenic%'))
).all()
```

### Find rare variants

```python
variants = db.query(Variant).filter(
    ((Variant.gnomad_af_all == None) | (Variant.gnomad_af_all < 0.0001))
).limit(100).all()
```

### Get variants by consequence

```python
lof_variants = db.query(Variant).filter(
    Variant.exonic_func.in_(['frameshift deletion', 'stopgain'])
).all()
```

### Generate statistics

```python
from sqlalchemy import func

total = db.query(func.count(Variant.variant_id)).scalar()
genes = db.query(func.count(func.distinct(Variant.gene_symbol))).scalar()
rare = db.query(func.count(Variant.variant_id)).filter(
    ((Variant.gnomad_af_all == None) | (Variant.gnomad_af_all < 0.0001))
).scalar()

print(f"Total variants: {total}")
print(f"Unique genes: {genes}")
print(f"Rare variants: {rare}")
```

## Troubleshooting

### "Module not found" errors

```bash
pip install -r requirements.txt --upgrade
```

### Database locked / Connection refused

For SQLite:
```bash
rm genomic_variants.db
python scripts/setup_database.py
```

For PostgreSQL:
```bash
psql -h localhost -U postgres
CREATE DATABASE genomic_variants;
\q
python scripts/setup_database.py
```

### Slow inserts

Reduce batch size in `.env`:
```
BATCH_SIZE=100
```

Or increase batch size if you have memory:
```
BATCH_SIZE=5000
```

### Large file won't load

Process in chunks:
```bash
# Split file
split -l 10000 large_file.tsv chunk_

# Load chunks
for file in chunk_*; do
    python scripts/load_intervar.py "$file"
done
```

## Next Steps

1. ✓ Setup database
2. ✓ Load data
3. ✓ Verify quality
4. → Build queries for your use case
5. → Integrate with FastAPI (optional)
6. → Add Qwen text-to-SQL (optional)

## Performance Tips

- Use indexes: all common query fields are indexed
- Batch operations: use bulk inserts
- Filter early: use WHERE clauses before JOINs
- Sample first: check with LIMIT before full queries
- Monitor logs: check `ingestion.log` for errors

## Support

- Check `ingestion.log` for load errors
- Run `verify_database.py` to check data quality
- Use `sample_queries.sql` as query templates
- Refer to README.md for detailed documentation
