#!/usr/bin/env python3
"""
PHASE 2: InterVar File Ingestion CLI
Entry point for loading InterVar TSV files into the database

Usage:
    python scripts/ingest_intervar.py --file /path/to/file.tsv [--source-id custom_id]
"""

import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path for app imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ingestion.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def main():
    """Main ingestion entry point"""
    parser = argparse.ArgumentParser(
        description="Load InterVar TSV file into genomic variant database"
    )
    
    parser.add_argument(
        '--file', '-f',
        required=True,
        help='Path to InterVar TSV/CSV file'
    )
    parser.add_argument(
        '--source-id', '-s',
        default=None,
        help='Custom source identifier (default: filename)'
    )
    parser.add_argument(
        '--chunk-size', '-c',
        type=int,
        default=10000,
        help='Rows per processing chunk (default: 10000)'
    )
    parser.add_argument(
        '--skip-validation',
        action='store_true',
        help='Skip row-level validation (faster, less safe)'
    )
    
    args = parser.parse_args()
    
    # Validate file
    file_path = Path(args.file)
    if not file_path.exists():
        logger.error(f"✗ File not found: {args.file}")
        sys.exit(1)
    
    logger.info(f"Genomic Variant Ingestion Pipeline")
    logger.info(f"File: {file_path.name}")
    logger.info(f"Size: {file_path.stat().st_size / (1024**3):.2f} GB")
    
    # Determine source ID
    source_id = args.source_id or file_path.stem
    logger.info(f"Source ID: {source_id}")
    
    try:
        # Import here to allow setup_database.py to run first
        from app.ingestion.loader_extended import InterVarLoader
        
        # Create loader
        loader = InterVarLoader(
            file_path=str(file_path),
            source_id=source_id
        )
        
        # Perform ingestion
        result = loader.ingest_file()
        
        # Print summary
        print("\n" + "="*70)
        print("INGESTION SUMMARY")
        print("="*70)
        print(f"Total rows processed:    {result['total_rows']:>10,}")
        print(f"Successfully inserted:   {result['inserted_rows']:>10,}")
        print(f"Skipped/duplicates:      {result['skipped_rows']:>10,}")
        print(f"Errors:                  {result['error_rows']:>10,}")
        print(f"Elapsed time:            {result['elapsed_seconds']:>10.1f}s")
        print(f"Rate:                    {result['rows_per_second']:>10.0f} rows/sec")
        print(f"Status:                  {result['status']:>10}")
        print("="*70 + "\n")
        
        sys.exit(0 if result['status'] == 'SUCCESS' else 1)
    
    except Exception as e:
        logger.error(f"✗ Ingestion failed: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
