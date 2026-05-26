#!/usr/bin/env python3
"""Monitor ingestion progress in real-time"""

import time
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

def monitor_database():
    """Monitor database population in real-time"""
    
    try:
        from app.database import SessionLocal
        from app.models import Variant, Gene, ImportLog
        
        print("\n" + "=" * 80)
        print("DATABASE POPULATION MONITOR")
        print("=" * 80 + "\n")
        
        db = SessionLocal()
        
        # Check every 5 seconds
        last_count = 0
        start_time = time.time()
        
        while True:
            try:
                # Get current counts
                variant_count = db.query(Variant).count()
                gene_count = db.query(Gene).count()
                import_logs = db.query(ImportLog).all()
                
                elapsed = time.time() - start_time
                
                print(f"\r[{elapsed:.0f}s] Variants: {variant_count:,} | Genes: {gene_count:,}", end='', flush=True)
                
                # Show import log details
                if import_logs:
                    latest = import_logs[-1]
                    if latest.total_rows:
                        percent = (latest.inserted_rows / latest.total_rows) * 100 if latest.total_rows > 0 else 0
                        print(f" | Progress: {latest.inserted_rows:,}/{latest.total_rows:,} ({percent:.1f}%)", end='', flush=True)
                        if latest.rows_per_second:
                            print(f" | Speed: {latest.rows_per_second:.0f} rows/sec", end='', flush=True)
                
                # Check if ingestion is complete
                if import_logs and import_logs[-1].import_status == 'completed':
                    print("\n\n✓ INGESTION COMPLETED!")
                    print(f"  Total variants: {variant_count:,}")
                    print(f"  Total genes: {gene_count:,}")
                    print(f"  Elapsed time: {elapsed:.0f}s")
                    break
                
                time.sleep(2)
                
            except Exception as e:
                print(f"\nMonitoring error: {e}")
                time.sleep(2)
        
        db.close()
        
    except ImportError as e:
        print(f"Import error (database may not be initialized): {e}")
        print("Waiting for database initialization...")
        time.sleep(5)
        monitor_database()

if __name__ == "__main__":
    monitor_database()
