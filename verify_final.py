"""Post-ingestion database verification"""
from app.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    vc = db.execute(text("SELECT COUNT(*) FROM variants")).scalar()
    gc = db.execute(text("SELECT COUNT(*) FROM genes")).scalar()
    ic = db.execute(text("SELECT COUNT(*) FROM import_logs")).scalar()
    print(f"Variants:    {vc:,}")
    print(f"Genes:       {gc:,}")
    print(f"ImportLogs:  {ic}")

    il = db.execute(text(
        "SELECT total_rows, inserted_rows, skipped_rows, error_rows, import_status "
        "FROM import_logs ORDER BY log_id DESC LIMIT 1"
    )).fetchone()
    if il:
        print(f"\nLast Import:")
        print(f"  Total rows:    {il[0]:,}")
        print(f"  Inserted:      {il[1]:,}")
        print(f"  Skipped:       {il[2]:,}")
        print(f"  Errors:        {il[3]:,}")
        print(f"  Status:        {il[4]}")

    b  = db.execute(text("SELECT COUNT(*) FROM variants WHERE intervar_classification LIKE '%Benign%'")).scalar()
    p  = db.execute(text("SELECT COUNT(*) FROM variants WHERE intervar_classification LIKE '%Pathogenic%'")).scalar()
    u  = db.execute(text("SELECT COUNT(*) FROM variants WHERE intervar_classification LIKE '%Uncertain%'")).scalar()
    print(f"\nClassification breakdown:")
    print(f"  Benign:      {b:,}")
    print(f"  Pathogenic:  {p:,}")
    print(f"  VUS:         {u:,}")

    top_genes = db.execute(text(
        "SELECT gene_symbol, COUNT(*) as cnt FROM variants "
        "WHERE gene_symbol IS NOT NULL GROUP BY gene_symbol ORDER BY cnt DESC LIMIT 5"
    )).fetchall()
    print(f"\nTop 5 genes by variant count:")
    for row in top_genes:
        print(f"  {row[0]}: {row[1]:,}")

    # Sample a real variant
    sample = db.execute(text(
        "SELECT variant_key, gene_symbol, intervar_classification, gnomad_af_all, cadd_phred "
        "FROM variants WHERE cadd_phred > 30 AND gene_symbol IS NOT NULL LIMIT 1"
    )).fetchone()
    if sample:
        print(f"\nSample high-impact variant:")
        print(f"  Key:    {sample[0]}")
        print(f"  Gene:   {sample[1]}")
        print(f"  Class:  {sample[2]}")
        print(f"  AF:     {sample[3]}")
        print(f"  CADD:   {sample[4]}")

finally:
    db.close()
