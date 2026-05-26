#!/usr/bin/env python3
"""
Quick database initialization and test
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

print("Step 1: Testing imports...")
try:
    from app.database import engine, SessionLocal, Base
    from app.models import Variant, Gene, ImportLog
    print("✓ Database imports successful")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

print("\nStep 2: Creating tables...")
try:
    Base.metadata.create_all(bind=engine)
    print("✓ Tables created")
except Exception as e:
    print(f"✗ Table creation failed: {e}")
    sys.exit(1)

print("\nStep 3: Testing database connection...")
try:
    db = SessionLocal()
    
    # Count records
    variant_count = db.query(Variant).count()
    gene_count = db.query(Gene).count()
    
    print(f"✓ Database connected")
    print(f"  - Variants: {variant_count}")
    print(f"  - Genes: {gene_count}")
    
    db.close()
except Exception as e:
    print(f"✗ Database connection failed: {e}")
    sys.exit(1)

print("\n✓ DATABASE SETUP COMPLETE - Ready for ingestion!")
