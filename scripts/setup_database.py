"""
Database Setup and Initialization Script
Creates all tables and loads initial metadata
"""

import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import engine, SessionLocal, Base
from app.models import Metadata
from app.ingestion.metadata_loader import (
    ACMGRuleLoader, ColumnDictionaryLoader, SourceVersionLoader
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def setup_database():
    """
    Initialize database:
    1. Create all tables
    2. Load metadata
    """
    logger.info("=" * 60)
    logger.info("GENOMIC VARIANT DATABASE SETUP")
    logger.info("=" * 60)
    
    try:
        # Drop and recreate tables (for development)
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✓ Tables created successfully")
        
        # Initialize metadata
        db = SessionLocal()
        
        logger.info("\nLoading metadata...")
        
        # Load ACMG rules
        logger.info("Loading ACMG rules...")
        ACMGRuleLoader.load_acmg_rules(db)
        
        # Load column dictionary
        logger.info("Loading column dictionary...")
        ColumnDictionaryLoader.load_column_dictionary(db)
        
        # Load source versions
        logger.info("Loading source versions...")
        SourceVersionLoader.load_source_versions(db)
        
        # Add system metadata
        logger.info("Initializing system metadata...")
        from datetime import datetime
        metadata = {
            'db_version': '1.0',
            'created_date': datetime.now().isoformat(),
            'schema_name': 'genomic_variants',
            'reference_genome': 'GRCh37/hg19',
        }
        
        for key, value in metadata.items():
            existing = db.query(Metadata).filter(Metadata.key == key).first()
            if not existing:
                meta = Metadata(key=key, value=value, data_type='string')
                db.add(meta)
        
        db.commit()
        db.close()
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ DATABASE SETUP COMPLETE")
        logger.info("=" * 60)
        logger.info("\nNext steps:")
        logger.info("1. Load InterVar data: python scripts/load_intervar.py <file_path>")
        logger.info("2. Verify data: python scripts/verify_database.py")
        logger.info("3. Run queries: python scripts/sample_queries.py")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Setup failed: {str(e)}", exc_info=True)
        return False


if __name__ == "__main__":
    success = setup_database()
    sys.exit(0 if success else 1)
