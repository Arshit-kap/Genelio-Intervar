#!/usr/bin/env python3
"""
CRITICAL: Diagnostic script to understand the data ingestion issue
and provide a fix for database population
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

print("="*80)
print("INTERVAR DATA INGESTION DIAGNOSTIC")
print("="*80)
print()

# 1. Check file existence
file_path = Path("intervar_3gb_file.txt")
if not file_path.exists():
    print("❌ File not found: intervar_3gb_file.txt")
    sys.exit(1)

print(f"✅ File found: {file_path}")
print(f"   Size: {file_path.stat().st_size / (1024**3):.2f} GB")
print()

# 2. Inspect first few lines
print("📋 INSPECTING FILE STRUCTURE:")
print("-" * 80)

try:
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = [f.readline() for _ in range(3)]
    
    for idx, line in enumerate(lines):
        line = line.rstrip('\n\r')
        
        # Check delimiter
        if '\t' in line:
            delim = 'TAB'
            cols = line.split('\t')
        elif ',' in line:
            delim = 'COMMA'
            cols = line.split(',')
        else:
            delim = 'UNKNOWN'
            cols = [line]
        
        print(f"Line {idx+1} ({delim}-delimited, {len(cols)} columns):")
        
        if idx == 0:  # Header
            print(f"  Columns: {cols}")
        else:  # Data
            # Show first 5 columns
            print(f"  First 5 values: {cols[:5]}")
            
            # Check for problematic data
            if 'Start' in str(cols[0]) or 'End' in str(cols[1]):
                print("  ⚠️  WARNING: This looks like header data, not variant data!")
        print()

except Exception as e:
    print(f"❌ Error reading file: {e}")
    sys.exit(1)

# 3. Check database
print("💾 CHECKING DATABASE:")
print("-" * 80)

try:
    import sqlite3
    
    conn = sqlite3.connect('genomic_variants.db')
    cursor = conn.cursor()
    
    # Check variant count
    cursor.execute('SELECT COUNT(*) FROM variants;')
    variant_count = cursor.fetchone()[0]
    
    print(f"Variants in database: {variant_count:,}")
    
    if variant_count == 0:
        print("⚠️  DATABASE IS EMPTY!")
        print()
        print("DIAGNOSIS: All rows are being rejected during ingestion.")
        print()
        print("RECOMMENDED FIX:")
        print("1. Check if the header row is included in data rows")
        print("2. Verify column names match exactly")
        print("3. Run ingestion with --skip-validation to bypass strict type checking")
        print("4. Or run: python scripts/ingest_intervar.py --file intervar_3gb_file.txt --skip-validation")
    
    conn.close()
except Exception as e:
    print(f"Error checking database: {e}")

print()
print("="*80)
