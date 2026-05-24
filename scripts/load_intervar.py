"""
InterVar File Ingestion Script
Load InterVar-formatted genomic variant data into PostgreSQL
"""

import logging
import sys
import argparse
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.ingestion.pipeline import VariantIngestionPipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ingestion.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def ingest_file(file_path: str, source_id: str = None):
    """
    Ingest a single InterVar file
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        logger.error(f"✗ File not found: {file_path}")
        return False
    
    logger.info("=" * 60)
    logger.info(f"INGESTION: {file_path.name}")
    logger.info("=" * 60)
    
    try:
        db = SessionLocal()
        pipeline = VariantIngestionPipeline(db)
        
        stats = pipeline.ingest_file(str(file_path), source_id)
        
        logger.info("\nINGESTION COMPLETE")
        logger.info("-" * 60)
        logger.info(f"Total rows:     {stats['total_rows']}")
        logger.info(f"Inserted:       {stats['inserted_rows']}")
        logger.info(f"Skipped:        {stats['skipped_rows']}")
        logger.info(f"Errors:         {stats['error_rows']}")
        
        if stats['errors']:
            logger.warning("\nFirst 5 errors:")
            for error in stats['errors'][:5]:
                logger.warning(f"  Row {error['row']}: {error['error']}")
        
        db.close()
        return True
        
    except Exception as e:
        logger.error(f"✗ Ingestion failed: {str(e)}", exc_info=True)
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Ingest InterVar genomic variant data into PostgreSQL database'
    )
    parser.add_argument('file', help='Path to InterVar TSV/CSV file')
    parser.add_argument('--source-id', default=None, help='Source identifier (default: filename)')
    
    args = parser.parse_args()
    
    success = ingest_file(args.file, args.source_id)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
