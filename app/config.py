"""
Application Configuration
Database connection and environment settings
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Database Configuration
DATABASE_URL = os.getenv(
    'DATABASE_URL',
    'postgresql://postgres:postgres@localhost:5432/genomic_variants'
)

# SQLite — prefer env var, then auto-detect patient_variants.db, fallback to legacy
SQLITE_DB = os.getenv('SQLITE_DB', 'genomic_variants.db')

def _resolve_db_url() -> str:
    """Resolve the SQLite database URL, preferring patient_variants.db if it exists."""
    # 1. Explicit override wins
    explicit = os.getenv('SQLALCHEMY_DATABASE_URL')
    if explicit:
        return explicit
    # 2. Auto-detect: prefer new patient DB over legacy
    workdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in ('patient_variants.db', 'genomic_variants.db'):
        path = os.path.join(workdir, candidate)
        if os.path.isfile(path):
            return f"sqlite:///{path}"
    # 3. Fallback to env/default name (relative path)
    return f"sqlite:///{SQLITE_DB}"

SQLALCHEMY_DATABASE_URL = _resolve_db_url()

# Ingestion Configuration
BATCH_SIZE = 1000
VALIDATE_ON_INSERT = True
SKIP_INVALID_ROWS = False

# Logging
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# File Paths
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'scripts')
