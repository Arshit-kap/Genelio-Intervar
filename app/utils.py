"""
Utility functions for QA and validation
"""

import logging
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from app.models import Variant, ImportLog, Gene, VariantInterpretation

logger = logging.getLogger(__name__)

def get_variant_stats(db: Session) -> dict:
    """Get overall variant database statistics"""
    try:
        total_variants = db.query(func.count(Variant.variant_id)).scalar()
        unique_genes = db.query(func.count(func.distinct(Variant.gene_id))).scalar()
        chromosomes = db.query(func.count(func.distinct(Variant.chromosome))).scalar()
        
        # Get frequency range
        freq_min = db.query(func.min(Variant.gnomad_af_all)).scalar()
        freq_max = db.query(func.max(Variant.gnomad_af_all)).scalar()
        
        # Get interpretation counts
        pathogenic = db.query(func.count(VariantInterpretation.interpretation_id)).filter(
            VariantInterpretation.classification.in_(['pathogenic', 'likely_pathogenic'])
        ).scalar()
        
        benign = db.query(func.count(VariantInterpretation.interpretation_id)).filter(
            VariantInterpretation.classification.in_(['benign', 'likely_benign'])
        ).scalar()
        
        return {
            'total_variants': total_variants,
            'unique_genes': unique_genes,
            'chromosomes': chromosomes,
            'freq_min': float(freq_min) if freq_min else None,
            'freq_max': float(freq_max) if freq_max else None,
            'pathogenic_interpretations': pathogenic,
            'benign_interpretations': benign,
        }
    except Exception as e:
        logger.error(f"Error getting variant stats: {str(e)}")
        return {}


def get_import_stats(db: Session) -> dict:
    """Get import history and statistics"""
    try:
        imports = db.query(ImportLog).all()
        
        total_imported = sum(log.inserted_rows for log in imports)
        total_errors = sum(log.error_rows for log in imports)
        
        stats = {
            'total_imports': len(imports),
            'total_imported': total_imported,
            'total_errors': total_errors,
            'import_history': []
        }
        
        for log in imports[-10:]:  # Last 10 imports
            stats['import_history'].append({
                'file': log.source_file,
                'rows': log.total_rows,
                'inserted': log.inserted_rows,
                'errors': log.error_rows,
                'status': log.import_status,
                'date': log.import_start.isoformat() if log.import_start else None
            })
        
        return stats
    except Exception as e:
        logger.error(f"Error getting import stats: {str(e)}")
        return {}


def validate_variant_quality(db: Session) -> dict:
    """Validate data quality in variants table"""
    checks = {
        'missing_chromosome': 0,
        'missing_position': 0,
        'invalid_positions': 0,
        'missing_alleles': 0,
        'missing_gene': 0,
        'total_checked': 0,
        'passed': True,
        'messages': []
    }
    
    try:
        variants = db.query(Variant).limit(1000).all()  # Sample check
        checks['total_checked'] = len(variants)
        
        for variant in variants:
            if not variant.chromosome:
                checks['missing_chromosome'] += 1
            if not variant.start_pos:
                checks['missing_position'] += 1
            if variant.start_pos and variant.end_pos and variant.start_pos > variant.end_pos:
                checks['invalid_positions'] += 1
            if not variant.ref_allele or not variant.alt_allele:
                checks['missing_alleles'] += 1
            if not variant.gene_symbol:
                checks['missing_gene'] += 1
        
        # Overall pass/fail
        error_count = (checks['missing_chromosome'] + checks['missing_position'] +
                      checks['invalid_positions'] + checks['missing_alleles'])
        if error_count > checks['total_checked'] * 0.05:  # >5% error rate
            checks['passed'] = False
            checks['messages'].append(f"Data quality issues: {error_count} errors in {checks['total_checked']} samples")
        else:
            checks['messages'].append("✓ Data quality check passed")
        
        return checks
    except Exception as e:
        logger.error(f"Error validating quality: {str(e)}")
        checks['passed'] = False
        checks['messages'].append(f"Validation error: {str(e)}")
        return checks


def sample_queries(db: Session) -> dict:
    """Run sample analytical queries"""
    queries = {
        'high_frequency_variants': [],
        'rare_variants': [],
        'pathogenic_variants': [],
        'top_genes': []
    }
    
    try:
        # High frequency variants
        high_freq = db.query(Variant).filter(
            Variant.gnomad_af_all > 0.01
        ).limit(5).all()
        queries['high_frequency_variants'] = [
            {
                'variant_key': v.variant_key,
                'gene': v.gene_symbol,
                'af': float(v.gnomad_af_all) if v.gnomad_af_all else None
            }
            for v in high_freq
        ]
        
        # Rare variants
        rare = db.query(Variant).filter(
            (Variant.gnomad_af_all == None) | (Variant.gnomad_af_all < 0.0001)
        ).limit(5).all()
        queries['rare_variants'] = [
            {
                'variant_key': v.variant_key,
                'gene': v.gene_symbol,
                'consequence': v.transcript_consequence
            }
            for v in rare
        ]
        
        # Pathogenic variants
        pathogenic = db.query(Variant).filter(
            Variant.clinvar_significance.ilike('%pathogenic%')
        ).limit(5).all()
        queries['pathogenic_variants'] = [
            {
                'variant_key': v.variant_key,
                'gene': v.gene_symbol,
                'significance': v.clinvar_significance
            }
            for v in pathogenic
        ]
        
        # Top genes
        top_genes = db.query(Variant.gene_symbol, func.count(Variant.gene_symbol).label('count')).filter(
            Variant.gene_symbol != None
        ).group_by(Variant.gene_symbol).order_by(func.count(Variant.gene_symbol).desc()).limit(5).all()
        queries['top_genes'] = [
            {
                'gene': g[0],
                'variant_count': g[1]
            }
            for g in top_genes
        ]
        
        return queries
    except Exception as e:
        logger.error(f"Error running sample queries: {str(e)}")
        return queries
