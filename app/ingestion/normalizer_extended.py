"""
PHASE 2: InterVar Data Normalizer
Apply all data cleaning and normalization rules
"""

import logging
import re
from typing import Dict, Any, Optional
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

class InterVarNormalizer:
    """
    Normalize and clean InterVar data according to ACMG guidelines
    Applies all rules for type conversion, NULL handling, etc.
    """
    
    # Chromosome validation
    VALID_CHROMOSOMES = set(
        [str(i) for i in range(1, 23)] + ['X', 'Y', 'MT', 'x', 'y', 'mt']
    )
    
    # Functional consequence mapping
    CONSEQUENCE_MAP = {
        'stopgain': 'loss_of_function',
        'stoploss': 'loss_of_function',
        'frameshift': 'loss_of_function',
        'frameshift insertion': 'loss_of_function',
        'frameshift deletion': 'loss_of_function',
        'missense': 'missense',
        'missense SNV': 'missense',
        'nonsynonymous SNV': 'missense',
        'synonymous': 'synonymous',
        'synonymous SNV': 'synonymous',
        'nonframeshift insertion': 'inframe_indel',
        'nonframeshift deletion': 'inframe_indel',
        'splicing': 'splice_site',
        'splice site': 'splice_site',
    }
    
    @staticmethod
    def safe_str(value: Any) -> Optional[str]:
        """Convert to string, None if . or empty"""
        if value is None or value == '.' or value == '':
            return None
        return str(value).strip()
    
    @staticmethod
    def safe_int(value: Any) -> Optional[int]:
        """Convert to integer, None if . or empty or not a number"""
        value_str = InterVarNormalizer.safe_str(value)
        if value_str is None:
            return None
        try:
            return int(float(value_str))
        except (ValueError, TypeError):
            logger.warning(f"Could not convert to int: {value}")
            return None
    
    @staticmethod
    def safe_float(value: Any) -> Optional[float]:
        """Convert to float, None if . or empty or not a number"""
        value_str = InterVarNormalizer.safe_str(value)
        if value_str is None:
            return None
        try:
            return float(value_str)
        except (ValueError, TypeError):
            logger.warning(f"Could not convert to float: {value}")
            return None
    
    @staticmethod
    def safe_decimal(value: Any, precision: int = 8) -> Optional[Decimal]:
        """Convert to Decimal with precision for frequencies"""
        value_str = InterVarNormalizer.safe_str(value)
        if value_str is None:
            return None
        try:
            dec = Decimal(value_str)
            # Ensure 0-1 range for frequencies
            if dec < 0 or dec > 1:
                logger.warning(f"Frequency out of range: {value}")
                return None
            return dec
        except InvalidOperation:
            logger.warning(f"Could not convert to Decimal: {value}")
            return None
    
    @staticmethod
    def normalize_chromosome(chr_val: Any) -> Optional[str]:
        """Normalize chromosome name"""
        chr_str = InterVarNormalizer.safe_str(chr_val)
        if chr_str is None:
            return None
        
        # Remove 'chr' prefix
        if chr_str.lower().startswith('chr'):
            chr_str = chr_str[3:]
        
        # Uppercase
        chr_str = chr_str.upper()
        
        if chr_str not in InterVarNormalizer.VALID_CHROMOSOMES:
            logger.warning(f"Invalid chromosome: {chr_val}")
            return None
        
        return chr_str
    
    @staticmethod
    def validate_genomic_position(chr_val: str, start: int, end: int) -> bool:
        """Validate genomic position logic"""
        if not chr_val or start is None or end is None:
            return False
        
        if start < 0 or end < 0:
            logger.warning(f"Negative positions: {chr_val}:{start}-{end}")
            return False
        
        if start > end:
            logger.warning(f"Inverted positions: {chr_val}:{start}-{end}")
            return False
        
        return True
    
    @staticmethod
    def validate_alleles(ref: str, alt: str) -> bool:
        """Validate allele sequences"""
        if not ref or not alt:
            return False

        # IUPAC codes + '-' for indel representation used by InterVar
        iupac = set('ACGTMRWSYKVHDBN-')
        ref_upper = ref.upper()
        alt_upper = alt.upper()

        for base in ref_upper:
            if base not in iupac:
                return False

        for base in alt_upper:
            if base not in iupac:
                return False

        return True
    
    @staticmethod
    def normalize_variant_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize a complete variant row
        Returns cleaned dict or None if validation fails
        """
        try:
            normalized = {}
            
            # GENOMIC COORDINATES (required)
            normalized['Chr'] = InterVarNormalizer.normalize_chromosome(row.get('Chr'))
            normalized['Start'] = InterVarNormalizer.safe_int(row.get('Start'))
            normalized['End'] = InterVarNormalizer.safe_int(row.get('End'))
            normalized['Ref'] = InterVarNormalizer.safe_str(row.get('Ref'))
            if normalized['Ref']:
                normalized['Ref'] = normalized['Ref'].upper()
            normalized['Alt'] = InterVarNormalizer.safe_str(row.get('Alt'))
            if normalized['Alt']:
                normalized['Alt'] = normalized['Alt'].upper()
            
            # Validate coordinates
            if not InterVarNormalizer.validate_genomic_position(
                normalized['Chr'], normalized['Start'], normalized['End']
            ):
                return None
            
            # Validate alleles
            if not InterVarNormalizer.validate_alleles(normalized['Ref'], normalized['Alt']):
                return None
            
            # IDENTIFIERS (optional)
            normalized['avsnp147'] = InterVarNormalizer.safe_str(row.get('avsnp147'))
            normalized['clinvar_ALLELEID'] = InterVarNormalizer.safe_str(row.get('clinvar_ALLELEID'))
            
            # GENE ANNOTATION
            normalized['Gene_refGene'] = InterVarNormalizer.safe_str(row.get('Ref_Gene'))
            normalized['Gene_ensGene'] = InterVarNormalizer.safe_str(row.get('Gene_ensGene'))
            normalized['AAChange_refGene'] = InterVarNormalizer.safe_str(row.get('AAChange_refGene'))
            normalized['AAChange_ensGene'] = InterVarNormalizer.safe_str(row.get('AAChange_ensGene'))
            
            # FUNCTIONAL CONSEQUENCE
            normalized['Func_refGene'] = InterVarNormalizer.safe_str(row.get('Func_refGene'))
            exonic_func = InterVarNormalizer.safe_str(row.get('ExonicFunc_refGene'))
            normalized['ExonicFunc_refGene'] = exonic_func
            # Map consequence for ACMG
            normalized['transcript_consequence'] = InterVarNormalizer.CONSEQUENCE_MAP.get(
                exonic_func.lower() if exonic_func else None, exonic_func
            )
            
            # FREQUENCIES - gnomAD (all populations as Decimal for precision)
            normalized['Freq_gnomAD_genome_ALL'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_gnomAD_genome_ALL')
            )
            normalized['Freq_gnomAD_genome_AFR'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_gnomAD_genome_AFR')
            )
            normalized['Freq_gnomAD_genome_ASJ'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_gnomAD_genome_ASJ')
            )
            normalized['Freq_gnomAD_genome_EAS'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_gnomAD_genome_EAS')
            )
            normalized['Freq_gnomAD_genome_FIN'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_gnomAD_genome_FIN')
            )
            normalized['Freq_gnomAD_genome_NFE'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_gnomAD_genome_NFE')
            )
            normalized['Freq_gnomAD_genome_OTH'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_gnomAD_genome_OTH')
            )
            normalized['Freq_gnomAD_genome_AMR'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_gnomAD_genome_AMR')
            )
            normalized['Freq_esp6500siv2_all'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_esp6500siv2_all')
            )
            normalized['Freq_1000g2015aug_all'] = InterVarNormalizer.safe_decimal(
                row.get('Freq_1000g2015aug_all')
            )
            
            # PREDICTION SCORES (as FLOAT)
            normalized['CADD_phred'] = InterVarNormalizer.safe_float(row.get('CADD_phred'))
            normalized['CADD_raw'] = InterVarNormalizer.safe_float(row.get('CADD_raw'))
            normalized['SIFT_score'] = InterVarNormalizer.safe_float(row.get('SIFT_score'))
            normalized['MetaSVM_score'] = InterVarNormalizer.safe_float(row.get('MetaSVM_score'))
            normalized['dbscSNV_ADA_SCORE'] = InterVarNormalizer.safe_float(
                row.get('dbscSNV_ADA_SCORE')
            )
            normalized['dbscSNV_RF_SCORE'] = InterVarNormalizer.safe_float(
                row.get('dbscSNV_RF_SCORE')
            )
            
            # CONSERVATION SCORES
            normalized['GERP_RS'] = InterVarNormalizer.safe_float(row.get('GERP_RS'))
            normalized['phyloP46way_placental'] = InterVarNormalizer.safe_float(
                row.get('phyloP46way_placental')
            )
            normalized['phyloP100way_vertebrate'] = InterVarNormalizer.safe_float(
                row.get('phyloP100way_vertebrate')
            )
            
            # STRUCTURE & DOMAIN
            normalized['Interpro_domain'] = InterVarNormalizer.safe_str(row.get('Interpro_domain'))
            normalized['rmsk'] = InterVarNormalizer.safe_str(row.get('rmsk'))
            
            # PHENOTYPE
            normalized['OMIM'] = InterVarNormalizer.safe_str(row.get('OMIM'))
            normalized['Otherinfo'] = InterVarNormalizer.safe_str(row.get('Otherinfo'))
            
            # CLINICAL SIGNIFICANCE
            normalized['clinvar_Clinvar'] = InterVarNormalizer.safe_str(row.get('clinvar_Clinvar'))
            normalized['InterVar'] = InterVarNormalizer.safe_str(row.get('InterVar_InterVar_and_Evidence'))
            
            # RAW ROW (for audit)
            normalized['raw_row_json'] = row
            
            return normalized
            
        except Exception as e:
            logger.error(f"Error normalizing row: {str(e)}")
            return None
