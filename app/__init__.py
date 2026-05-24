"""
Genomic Variant Interpretation System
SQL-first production database for InterVar and ClinVar integration
"""

__version__ = "1.0.0"
__author__ = "Genomic QA Team"

from app.database import SessionLocal, engine, Base
from app.config import SQLALCHEMY_DATABASE_URL

__all__ = [
    'SessionLocal',
    'engine',
    'Base',
    'SQLALCHEMY_DATABASE_URL'
]
