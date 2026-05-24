"""
Variant Data Parsing and Normalization
Handles InterVar format data cleaning, validation, and field parsing
"""

import json
import re
from typing import Dict, Any, Optional, Tuple, List
from decimal import Decimal, InvalidOperation
import logging

logger = logging.getLogger(__name__)

class VariantParser:
    """Parse and normalize InterVar format variants"""
    
    @staticmethod
    def generate_variant_key(chromosome: str, start_pos: int, ref: str, alt: str) -> str:
        """Generate unique variant key"""
        return f"{chromosome}:{start_pos}:{ref}:{alt}".upper()
    
    @staticmethod
    def safe_int(value: Any) -> Optional[int]:
        """Convert value to int, return None for invalid/empty"""
        if value is None or value == '.' or value == '':
            return None
        try:
            return int(float(str(value).strip()))
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def safe_float(value: Any) -> Optional[float]:
        """Convert value to float, return None for invalid/empty"""
        if value is None or value == '.' or value == '':
            return None
        try:
            return float(str(value).strip())
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def safe_numeric(value: Any) -> Optional[Decimal]:
        """Convert value to Decimal for high-precision storage"""
        if value is None or value == '.' or value == '':
            return None
        try:
            return Decimal(str(value).strip())
        except (InvalidOperation, ValueError, TypeError):
            return None
    
    @staticmethod
    def safe_string(value: Any) -> Optional[str]:
        """Convert value to string, return None for empty/missing"""
        if value is None:
            return None
        s = str(value).strip()
        if s == '.' or s == '':
            return None
        return s
    
    @staticmethod
    def parse_gnomad_frequencies(freq_str: str) -> Dict[str, Optional[Decimal]]:
        """
        Parse gnomAD frequency string format.
        Expected format: AF=0.001;AF_afr=0.002;AF_asj=0.003; etc.
        or: 0.001,0.002,0.003,... (positional)
        """
        frequencies = {
            'gnomad_af_all': None,
            'gnomad_af_afr': None,
            'gnomad_af_asj': None,
            'gnomad_af_eas': None,
            'gnomad_af_fin': None,
            'gnomad_af_nfe': None,
            'gnomad_af_oth': None,
            'gnomad_af_amr': None,
        }
        
        if not freq_str or freq_str == '.' or freq_str == '':
            return frequencies
        
        freq_str = str(freq_str).strip()
        
        # Try semicolon-delimited format (e.g., "AF=0.001;AF_afr=0.002")
        if ';' in freq_str:
            parts = freq_str.split(';')
            for part in parts:
                if '=' in part:
                    key, val = part.split('=', 1)
                    key = key.strip().lower()
                    
                    # Map to our column names
                    mapping = {
                        'af': 'gnomad_af_all',
                        'af_all': 'gnomad_af_all',
                        'af_afr': 'gnomad_af_afr',
                        'af_asj': 'gnomad_af_asj',
                        'af_eas': 'gnomad_af_eas',
                        'af_fin': 'gnomad_af_fin',
                        'af_nfe': 'gnomad_af_nfe',
                        'af_oth': 'gnomad_af_oth',
                        'af_amr': 'gnomad_af_amr',
                    }
                    
                    if key in mapping:
                        frequencies[mapping[key]] = VariantParser.safe_numeric(val)
        
        # Try comma-delimited format
        elif ',' in freq_str:
            parts = [p.strip() for p in freq_str.split(',')]
            pop_order = ['gnomad_af_all', 'gnomad_af_afr', 'gnomad_af_asj', 'gnomad_af_eas',
                         'gnomad_af_fin', 'gnomad_af_nfe', 'gnomad_af_oth', 'gnomad_af_amr']
            for i, part in enumerate(parts):
                if i < len(pop_order) and part:
                    frequencies[pop_order[i]] = VariantParser.safe_numeric(part)
        
        # Try single value (ALL frequency)
        else:
            frequencies['gnomad_af_all'] = VariantParser.safe_numeric(freq_str)
        
        return frequencies
    
    @staticmethod
    def parse_intervar_evidence(intervar_str: str) -> Dict[str, bool]:
        """
        Parse InterVar evidence string for ACMG criteria.
        Expected format: "PP4,PM2,PP3" or individual boolean fields
        """
        acmg_criteria = {
            # Pathogenic
            'PVS1': False, 'PS1': False, 'PS2': False, 'PS3': False, 'PS4': False,
            'PM1': False, 'PM2': False, 'PM4': False, 'PM5': False, 'PM6': False,
            'PP1': False, 'PP2': False, 'PP3': False, 'PP4': False, 'PP5': False,
            # Benign
            'BA1': False, 'BS1': False, 'BS2': False, 'BS3': False, 'BS4': False,
            'BP1': False, 'BP2': False, 'BP3': False, 'BP4': False, 'BP5': False, 'BP6': False, 'BP7': False,
        }
        
        if not intervar_str or intervar_str == '.' or intervar_str == '':
            return acmg_criteria
        
        intervar_str = str(intervar_str).strip().upper()
        
        # Parse comma or semicolon-delimited criteria
        delimiters = [',', ';', '|']
        for delimiter in delimiters:
            if delimiter in intervar_str:
                criteria_found = intervar_str.split(delimiter)
                for criterion in criteria_found:
                    criterion = criterion.strip()
                    if criterion in acmg_criteria:
                        acmg_criteria[criterion] = True
                return acmg_criteria
        
        # Try space-delimited
        criteria_found = intervar_str.split()
        for criterion in criteria_found:
            criterion = criterion.strip()
            if criterion in acmg_criteria:
                acmg_criteria[criterion] = True
        
        return acmg_criteria
    
    @staticmethod
    def parse_computational_scores(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """
        Parse computational prediction scores
        CADD, SIFT, MetaSVM, dbscSNV
        """
        scores = {
            'cadd_phred': None,
            'cadd_raw': None,
            'sift_score': None,
            'sift_pred': None,
            'metasvm_score': None,
            'metasvm_pred': None,
            'dbscsnv_ada_score': None,
            'dbscsnv_rf_score': None,
        }
        
        # CADD scores
        scores['cadd_phred'] = VariantParser.safe_float(row.get('CADD_phred', row.get('CADD_PHRED')))
        scores['cadd_raw'] = VariantParser.safe_float(row.get('CADD_raw', row.get('CADD_RAW')))
        
        # SIFT
        sift_val = row.get('SIFT_score', row.get('SIFT', ''))
        if sift_val and sift_val != '.':
            # Format might be "deleterious(0.02)" or just "0.02"
            match = re.search(r'[\d.]+', str(sift_val))
            if match:
                scores['sift_score'] = VariantParser.safe_float(match.group())
            # Store prediction
            if 'deleterious' in str(sift_val).lower():
                scores['sift_pred'] = 'deleterious'
            elif 'tolerated' in str(sift_val).lower():
                scores['sift_pred'] = 'tolerated'
        
        # MetaSVM
        metasvm_val = row.get('MetaSVM_score', row.get('MetaSVM', ''))
        if metasvm_val and metasvm_val != '.':
            match = re.search(r'[-\d.]+', str(metasvm_val))
            if match:
                scores['metasvm_score'] = VariantParser.safe_float(match.group())
            if 'deleterious' in str(metasvm_val).lower():
                scores['metasvm_pred'] = 'deleterious'
            elif 'tolerated' in str(metasvm_val).lower():
                scores['metasvm_pred'] = 'tolerated'
        
        # dbscSNV scores
        scores['dbscsnv_ada_score'] = VariantParser.safe_float(row.get('dbscSNV_ADA_SCORE', row.get('dbscSNV_ADA')))
        scores['dbscsnv_rf_score'] = VariantParser.safe_float(row.get('dbscSNV_RF_SCORE', row.get('dbscSNV_RF')))
        
        return scores
    
    @staticmethod
    def parse_conservation_scores(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Parse conservation scores: GERP++, PhyloP, etc."""
        conservation = {
            'gerp_rs': None,
            'phylop46way_placental': None,
            'phylop100way_vertebrate': None,
        }
        
        conservation['gerp_rs'] = VariantParser.safe_float(row.get('GERP++_RS', row.get('GERP_RS')))
        conservation['phylop46way_placental'] = VariantParser.safe_float(
            row.get('phyloP46way_placental', row.get('phyloP46way_placental_rankscore'))
        )
        conservation['phylop100way_vertebrate'] = VariantParser.safe_float(
            row.get('phyloP100way_vertebrate', row.get('phyloP100way_vertebrate_rankscore'))
        )
        
        return conservation
    
    @staticmethod
    def parse_transcript_consequence(exonic_func: str, func_region: str) -> str:
        """
        Infer transcript consequence from ExonicFunc and Func columns
        """
        consequence = None
        
        if exonic_func and exonic_func != '.':
            exonic_func_lower = str(exonic_func).lower()
            if 'stopgain' in exonic_func_lower or 'stoploss' in exonic_func_lower:
                consequence = 'stop_gained' if 'stopgain' in exonic_func_lower else 'stop_lost'
            elif 'frameshift' in exonic_func_lower:
                consequence = 'frameshift_variant'
            elif 'inframe' in exonic_func_lower:
                consequence = 'inframe_indel'
            elif 'missense' in exonic_func_lower:
                consequence = 'missense_variant'
            elif 'synonymous' in exonic_func_lower or 'silent' in exonic_func_lower:
                consequence = 'synonymous_variant'
        
        if not consequence and func_region and func_region != '.':
            func_region_lower = str(func_region).lower()
            if 'splicing' in func_region_lower:
                consequence = 'splicing_variant'
            elif 'upstream' in func_region_lower or 'downstream' in func_region_lower:
                consequence = 'regulatory_region'
            elif 'intron' in func_region_lower:
                consequence = 'intron_variant'
        
        return consequence


class VariantValidator:
    """Validate variant records"""
    
    REQUIRED_COLUMNS = [
        'Chr', 'Start', 'End', 'Ref', 'Alt'
    ]
    
    @staticmethod
    def validate_row(row: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        Validate a single variant row.
        Returns: (is_valid, error_message)
        """
        # Check required columns
        for col in VariantValidator.REQUIRED_COLUMNS:
            if col not in row or not row[col] or row[col] == '.':
                return False, f"Missing required column: {col}"
        
        # Validate chromosome
        chr_val = str(row['Chr']).strip()
        if not re.match(r'^(chr)?([1-9]|1[0-9]|2[0-2]|X|Y|M|MT)$', chr_val, re.IGNORECASE):
            return False, f"Invalid chromosome: {chr_val}"
        
        # Validate positions
        try:
            start = int(row['Start'])
            end = int(row['End'])
            if start < 0 or end < start:
                return False, f"Invalid positions: {start}-{end}"
        except (ValueError, TypeError):
            return False, "Start/End are not integers"
        
        # Validate alleles
        ref = str(row['Ref']).strip().upper()
        alt = str(row['Alt']).strip().upper()
        
        if not ref or not alt or ref == '.' or alt == '.':
            return False, "Invalid ref or alt allele"
        
        if not re.match(r'^[ACGT]+$', ref) or not re.match(r'^[ACGT*]+$', alt):
            return False, "Ref/Alt contain non-IUPAC characters"
        
        return True, None
