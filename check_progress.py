#!/usr/bin/env python3
"""
Monitor database ingestion progress without interfering
"""
import sqlite3
import sys
from pathlib import Path
import time

def check_database_progress():
    """Check how many records have been inserted"""
    
    db_file = Path("./genomic_variants.db")
    
    if not db_file.exists():
        print("⏳ Database file not yet created. Waiting for initialization...")
        return
    
    try:
        conn = sqlite3.connect(str(db_file))
        cursor = conn.cursor()
        
        # Check variant count
        cursor.execute("SELECT COUNT(*) FROM variants;")
        variant_count = cursor.fetchone()[0]
        
        # Check gene count
        cursor.execute("SELECT COUNT(*) FROM genes;")
        gene_count = cursor.fetchone()[0]
        
        # Check import log
        cursor.execute("""
            SELECT total_rows, inserted_rows, skipped_rows, error_rows, 
                   rows_per_second, import_status, created_at
            FROM import_logs
            ORDER BY created_at DESC
            LIMIT 1;
        """)
        
        import_log = cursor.fetchone()
        
        print(f"\n{'='*70}")
        print(f"DATABASE INGESTION PROGRESS")
        print(f"{'='*70}")
        print(f"Variants inserted:     {variant_count:>12,}")
        print(f"Genes created:         {gene_count:>12,}")
        
        if import_log:
            total, inserted, skipped, errors, speed, status, created = import_log
            if total > 0:
                percent = (inserted / total) * 100
                print(f"\nImport Status:")
                print(f"  Total rows:          {total:>12,}")
                print(f"  Inserted:            {inserted:>12,} ({percent:.1f}%)")
                print(f"  Skipped/Duplicates:  {skipped:>12,}")
                print(f"  Errors:              {errors:>12,}")
                print(f"  Speed:               {speed:>12.0f} rows/sec")
                print(f"  Status:              {status:>12}")
                print(f"  Started:             {created}")
        
        print(f"{'='*70}\n")
        
        conn.close()
        
    except sqlite3.OperationalError as e:
        print(f"⏳ Database is being initialized: {e}")
    except Exception as e:
        print(f"❌ Error checking database: {e}")

if __name__ == "__main__":
    check_database_progress()
