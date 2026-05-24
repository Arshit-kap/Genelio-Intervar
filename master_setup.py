"""
Master Setup Script - Complete System Initialization
Run this once to set up the entire genomic variant database
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def print_banner():
    """Print welcome banner"""
    print("\n" + "=" * 70)
    print("GENOMIC VARIANT INTERPRETATION DATABASE - COMPLETE SETUP")
    print("=" * 70 + "\n")

def setup_python_environment():
    """Check and setup Python environment"""
    logger.info("Checking Python environment...")
    
    try:
        import sqlalchemy
        import pandas
        import pydantic
        logger.info("✓ Python environment is properly configured")
        return True
    except ImportError as e:
        logger.error(f"✗ Missing dependency: {str(e)}")
        logger.error("Run: pip install -r requirements.txt")
        return False

def setup_database():
    """Initialize database and metadata"""
    logger.info("\nSetting up database...")
    
    try:
        from app.database import engine, SessionLocal, Base
        from app.models import Metadata
        from app.ingestion.metadata_loader_extended import (
            ExtendedACMGRuleLoader, ExtendedColumnDictionaryLoader, ExtendedSourceVersionLoader
        )
        
        # Create tables
        logger.info("Creating tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✓ Tables created")
        
        # Initialize metadata
        db = SessionLocal()
        
        logger.info("Loading ACMG rules (28 evidence codes)...")
        ExtendedACMGRuleLoader.load_acmg_rules(db)
        
        logger.info("Loading column dictionary (34 InterVar fields)...")
        ExtendedColumnDictionaryLoader.load_column_dictionary(db)
        
        logger.info("Loading source versions...")
        ExtendedSourceVersionLoader.load_source_versions(db)
        
        # System metadata
        from datetime import datetime
        
        metadata_dict = {
            'db_version': '1.0',
            'created_date': datetime.now().isoformat(),
            'schema_name': 'genomic_variants',
            'reference_genome': 'GRCh37/hg19',
            'setup_complete': 'true'
        }
        
        for key, value in metadata_dict.items():
            existing = db.query(Metadata).filter(Metadata.key == key).first()
            if not existing:
                meta = Metadata(key=key, value=value, data_type='string')
                db.add(meta)
        
        db.commit()
        db.close()
        
        logger.info("✓ Database initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"✗ Database setup failed: {str(e)}")
        return False

def create_example_data():
    """Create example InterVar data file"""
    logger.info("\nCreating example data file...")
    
    example_file = Path(__file__).parent / "example_data.tsv"
    
    try:
        with open(example_file, 'w') as f:
            # Header
            f.write("\t".join([
                "Chr", "Start", "End", "Ref", "Alt",
                "Gene.refGene", "ExonicFunc.refGene", "Func.refGene",
                "clinvar: Clinvar", "InterVar", "Freq_gnomAD_genome_POPs",
                "CADD_phred", "dbSNP147", "SIFT_score", "MetaSVM_score"
            ]) + "\n")
            
            # Example variant
            f.write("\t".join([
                "1", "100000", "100000", "A", "T",
                "GENE1", "missense", "exonic",
                "pathogenic", "pathogenic(PVS1,PM2)",
                "AF=0.0001;AF_afr=0.0002;AF_nfe=0.00001",
                "25.3", "rs123456", "0.02", "0.7"
            ]) + "\n")
        
        logger.info(f"✓ Example file created: {example_file}")
        return True
        
    except Exception as e:
        logger.error(f"✗ Failed to create example: {str(e)}")
        return False

def print_next_steps():
    """Print instructions for next steps"""
    print("\n" + "=" * 70)
    print("SETUP COMPLETE!")
    print("=" * 70 + "\n")
    
    print("Next steps:\n")
    print("1. LOAD YOUR DATA:")
    print("   python scripts/load_intervar.py your_file.tsv\n")
    print("2. VERIFY INSTALLATION:")
    print("   python scripts/verify_database.py\n")
    print("3. RUN SAMPLE QUERIES:")
    print("   sqlite3 genomic_variants.db < scripts/sample_queries.sql\n")
    print("4. LOAD EXAMPLE DATA (optional):")
    print("   python scripts/load_intervar.py example_data.tsv\n")
    
    print("DOCUMENTATION:")
    print("  - QUICKSTART.md    - Quick start guide")
    print("  - README.md        - Complete documentation")
    print("  - IMPLEMENTATION.md - Architecture overview")
    print("\n" + "=" * 70 + "\n")

def main():
    """Main setup function"""
    print_banner()
    
    # Check environment
    if not setup_python_environment():
        logger.error("\n✗ Setup failed due to missing dependencies")
        logger.error("Please run: pip install -r requirements.txt")
        return False
    
    # Setup database
    if not setup_database():
        logger.error("\n✗ Database setup failed")
        return False
    
    # Create example
    create_example_data()
    
    # Print next steps
    print_next_steps()
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
