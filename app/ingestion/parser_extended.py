"""
PHASE 2: InterVar File Parser
Chunks large TSV files for memory-efficient processing
Handles 3GB+ files without memory overflow
"""

import logging
import csv
from typing import Iterator, Dict, List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

class InterVarParser:
    """
    Parse InterVar TSV files in chunks
    Returns dictionaries with sanitized column names
    """
    
    def __init__(self, file_path: str, chunk_size: int = 10000):
        self.file_path = Path(file_path)
        self.chunk_size = chunk_size
        self.total_rows = 0
        self.current_chunk = 0
        
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
    
    def detect_delimiter(self) -> str:
        """Detect if file is tab-delimited or comma-delimited"""
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                if '\t' in first_line and ',' not in first_line:
                    return '\t'
                elif ',' in first_line:
                    return ','
                else:
                    return '\t'  # default to tab
        except Exception as e:
            logger.warning(f"Could not detect delimiter, defaulting to tab: {e}")
            return '\t'
    
    def sanitize_column_name(self, col_name: str) -> str:
        """Convert InterVar column names to SQL-safe names"""
        # Strip surrounding whitespace and leading # (e.g. #Chr header)
        sanitized = col_name.strip().lstrip('#')
        # Replace special characters with underscores
        sanitized = sanitized.replace(':', '_').replace(' ', '_').replace('.', '_')
        sanitized = sanitized.replace('(', '').replace(')', '').replace('+', '')
        # Collapse multiple consecutive underscores
        while '__' in sanitized:
            sanitized = sanitized.replace('__', '_')
        # Strip leading/trailing underscores
        sanitized = sanitized.strip('_')
        return sanitized
    
    def parse_chunks(self) -> Iterator[List[Dict[str, str]]]:
        """
        Yield chunks of parsed rows
        Each chunk is a list of dictionaries with sanitized keys
        """
        delimiter = self.detect_delimiter()
        delim_name = "TAB" if delimiter == "\t" else "COMMA"
        logger.info(f"Detected delimiter: {delim_name}")
        
        try:
            with open(self.file_path, 'r', encoding='utf-8', newline='') as f:
                reader = csv.DictReader(f, delimiter=delimiter)
                
                if reader.fieldnames is None:
                    raise ValueError("File appears to be empty or has no headers")
                
                # Sanitize header names
                sanitized_headers = {
                    old: self.sanitize_column_name(old)
                    for old in reader.fieldnames
                }
                logger.info(f"✓ Found {len(sanitized_headers)} columns")
                logger.debug(f"Column mapping: {sanitized_headers}")
                
                chunk = []
                row_count = 0
                
                for row in reader:
                    # Sanitize row keys
                    sanitized_row = {
                        sanitized_headers[old_key]: value
                        for old_key, value in row.items()
                    }
                    
                    chunk.append(sanitized_row)
                    row_count += 1
                    
                    if len(chunk) >= self.chunk_size:
                        self.current_chunk += 1
                        logger.info(f"Parsed chunk {self.current_chunk}: {row_count} rows")
                        yield chunk
                        chunk = []
                
                # Yield remaining rows
                if chunk:
                    self.current_chunk += 1
                    logger.info(f"Parsed chunk {self.current_chunk} (final): {len(chunk)} rows")
                    yield chunk
                
                self.total_rows = row_count
                logger.info(f"✓ Total rows parsed: {self.total_rows}")
                
        except Exception as e:
            logger.error(f"✗ Error parsing file: {str(e)}")
            raise
    
    def get_total_rows(self) -> int:
        """Count total rows in file (for progress tracking)"""
        if self.total_rows > 0:
            return self.total_rows
        
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                self.total_rows = sum(1 for line in f) - 1  # Subtract header
            return self.total_rows
        except Exception as e:
            logger.warning(f"Could not count rows: {e}")
            return 0
