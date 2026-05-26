#!/usr/bin/env python3
import sqlite3
import os

db_file = 'genomic_variants.db'

if not os.path.exists(db_file):
    print('❌ Database file does not exist')
    exit(1)

try:
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    # Check variant count
    cursor.execute('SELECT COUNT(*) FROM variants;')
    variant_count = cursor.fetchone()[0]
    
    # Check gene count
    cursor.execute('SELECT COUNT(*) FROM genes;')
    gene_count = cursor.fetchone()[0]
    
    # Check metadata
    cursor.execute('SELECT COUNT(*) FROM metadata;')
    metadata_count = cursor.fetchone()[0]
    
    # Check import log
    cursor.execute('SELECT COUNT(*) FROM import_logs;')
    import_log_count = cursor.fetchone()[0]
    
    # Sample variant if exists
    sample_variant = None
    if variant_count > 0:
        cursor.execute('SELECT variant_key, gene_symbol, intervar_classification FROM variants LIMIT 1;')
        sample_variant = cursor.fetchone()
    
    print('='*70)
    print('DATABASE STATUS REPORT')
    print('='*70)
    print(f'✅ Database file: {db_file}')
    print(f'   File size: {os.path.getsize(db_file) / (1024**3):.2f} GB')
    print()
    print('📊 DATA COUNTS:')
    print(f'   Variants inserted: {variant_count:,}')
    print(f'   Genes created: {gene_count:,}')
    print(f'   Metadata entries: {metadata_count:,}')
    print(f'   Import logs: {import_log_count:,}')
    print()
    
    if variant_count > 0:
        print('✅ DATABASE IS POPULATED!')
        if sample_variant:
            print(f'\n   Sample variant:')
            print(f'   - variant_key: {sample_variant[0]}')
            print(f'   - gene_symbol: {sample_variant[1]}')
            print(f'   - classification: {sample_variant[2]}')
    else:
        print('⚠️  Database is EMPTY - Data has NOT been inserted')
    
    print()
    print('='*70)
    
    conn.close()
except Exception as e:
    print(f'❌ Error: {e}')
    import traceback
    traceback.print_exc()
