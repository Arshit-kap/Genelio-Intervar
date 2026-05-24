"""
Database Verification Script
QA checks and sample queries
"""

import logging
import sys
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.utils import (
    get_variant_stats, get_import_stats, validate_variant_quality, sample_queries
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def verify_database():
    """
    Verify database integrity and run QA checks
    """
    logger.info("=" * 60)
    logger.info("DATABASE VERIFICATION")
    logger.info("=" * 60)
    
    try:
        db = SessionLocal()
        
        # Get statistics
        logger.info("\nVARIANT STATISTICS")
        logger.info("-" * 60)
        stats = get_variant_stats(db)
        for key, value in stats.items():
            logger.info(f"{key:.<40} {value}")
        
        # Get import history
        logger.info("\nIMPORT HISTORY")
        logger.info("-" * 60)
        import_stats = get_import_stats(db)
        logger.info(f"Total imports:.<40} {import_stats.get('total_imports', 0)}")
        logger.info(f"Total imported:.<40} {import_stats.get('total_imported', 0)}")
        logger.info(f"Total errors:.<40} {import_stats.get('total_errors', 0)}")
        
        if import_stats.get('import_history'):
            logger.info("\nLast imports:")
            for imp in import_stats['import_history'][-3:]:
                logger.info(f"  {imp['file']}: {imp['inserted']} inserted, {imp['errors']} errors")
        
        # Quality check
        logger.info("\nDATA QUALITY CHECK")
        logger.info("-" * 60)
        quality = validate_variant_quality(db)
        logger.info(f"Samples checked:.<40} {quality['total_checked']}")
        logger.info(f"Missing chromosome:.<40} {quality['missing_chromosome']}")
        logger.info(f"Missing position:.<40} {quality['missing_position']}")
        logger.info(f"Invalid positions:.<40} {quality['invalid_positions']}")
        logger.info(f"Missing alleles:.<40} {quality['missing_alleles']}")
        logger.info(f"Status: {'✓ PASS' if quality['passed'] else '✗ FAIL'}")
        
        # Sample queries
        logger.info("\nSAMPLE QUERIES")
        logger.info("-" * 60)
        queries = sample_queries(db)
        
        if queries['high_frequency_variants']:
            logger.info("High frequency variants (AF > 1%):")
            for v in queries['high_frequency_variants']:
                logger.info(f"  {v['variant_key']:.<40} {v['gene']:.<15} AF={v['af']}")
        
        if queries['rare_variants']:
            logger.info("\nRare variants (AF < 0.01%):")
            for v in queries['rare_variants'][:3]:
                logger.info(f"  {v['variant_key']}")
        
        if queries['top_genes']:
            logger.info("\nTop genes by variant count:")
            for g in queries['top_genes']:
                logger.info(f"  {g['gene']:.<30} {g['variant_count']} variants")
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ VERIFICATION COMPLETE")
        logger.info("=" * 60)
        
        db.close()
        return True
        
    except Exception as e:
        logger.error(f"✗ Verification failed: {str(e)}", exc_info=True)
        return False


if __name__ == "__main__":
    success = verify_database()
    sys.exit(0 if success else 1)
