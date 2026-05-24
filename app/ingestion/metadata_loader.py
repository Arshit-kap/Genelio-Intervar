"""
Metadata Loaders
Load ACMG rules, column dictionary, and source versions
"""

import csv
import logging
from typing import List, Dict, Any
from sqlalchemy.orm import Session

from app.models import ACMGRuleMap, ColumnDictionary, SourceVersion

logger = logging.getLogger(__name__)

class ACMGRuleLoader:
    """Load ACMG rule mappings from CSV"""
    
    ACMG_RULES_DATA = [
        # Population-based
        {'code': 'BA1', 'category': 'Population', 'pathogenic': False, 'strength': 'Very Strong (B)',
         'description': 'Allele frequency > 5% in gnomAD', 'threshold': 'Freq_gnomAD > 0.05'},
        {'code': 'BS1', 'category': 'Population', 'pathogenic': False, 'strength': 'Strong (B)',
         'description': 'Frequency higher than expected for disorder', 'threshold': 'Freq_gnomAD > disease_threshold'},
        {'code': 'BS2', 'category': 'Population', 'pathogenic': False, 'strength': 'Strong (B)',
         'description': 'Observed in healthy adult cohorts', 'threshold': 'Presence in large healthy cohorts'},
        {'code': 'PM2', 'category': 'Population', 'pathogenic': True, 'strength': 'Moderate (P)',
         'description': 'Frequency is 0 or very low (typically not seen)', 'threshold': 'Freq_gnomAD == 0 or missing'},
        {'code': 'PS4', 'category': 'Population', 'pathogenic': True, 'strength': 'Strong (P)',
         'description': 'Odds ratio > 5.0 with p-value < 0.05', 'threshold': 'OR > 5.0 (p < 0.05)'},
        
        # Computational/Functional
        {'code': 'PVS1', 'category': 'Computational', 'pathogenic': True, 'strength': 'Very Strong (P)',
         'description': 'Null variant in LOF intolerant gene', 'threshold': 'ExonicFunc=stopgain/frameshift + LOF'},
        {'code': 'PS1', 'category': 'Computational', 'pathogenic': True, 'strength': 'Strong (P)',
         'description': 'Amino acid change matches known pathogenic', 'threshold': 'Same amino acid change as ClinVar P'},
        {'code': 'PM1', 'category': 'Functional', 'pathogenic': True, 'strength': 'Moderate (P)',
         'description': 'Located in functional domain/motif', 'threshold': 'Interpro_domain non-empty'},
        {'code': 'PM4', 'category': 'Computational', 'pathogenic': True, 'strength': 'Moderate (P)',
         'description': 'In-frame indel in non-repeat region', 'threshold': 'ExonicFunc=inframe + rmsk empty'},
        {'code': 'PM5', 'category': 'Computational', 'pathogenic': True, 'strength': 'Moderate (P)',
         'description': 'Different nucleotide change, same amino acid as pathogenic', 'threshold': 'Same AA as ClinVar P'},
        {'code': 'PM6', 'category': 'Functional', 'pathogenic': True, 'strength': 'Moderate (P)',
         'description': 'Assumed de novo (no genomic DNA available)', 'threshold': 'De novo status'},
        {'code': 'PP3', 'category': 'Computational', 'pathogenic': True, 'strength': 'Supporting (P)',
         'description': 'Multiple computational predictors predict damaging', 'threshold': 'CADD > 20, SIFT < 0.05'},
        {'code': 'PP4', 'category': 'Functional', 'pathogenic': True, 'strength': 'Supporting (P)',
         'description': 'Patient phenotype or family history consistent with condition', 'threshold': 'Phenotype match'},
        
        # Benign predictions
        {'code': 'BP1', 'category': 'Computational', 'pathogenic': False, 'strength': 'Supporting (B)',
         'description': 'Missense variant in gene where missense NOT known disease cause', 'threshold': 'OMIM data'},
        {'code': 'BP3', 'category': 'Computational', 'pathogenic': False, 'strength': 'Supporting (B)',
         'description': 'In-frame indels in repeat region (may be benign)', 'threshold': 'ExonicFunc=inframe + rmsk'},
        {'code': 'BP4', 'category': 'Computational', 'pathogenic': False, 'strength': 'Supporting (B)',
         'description': 'Multiple lines of computational evidence suggest benign', 'threshold': 'CADD < 10, SIFT > 0.05'},
        {'code': 'BP7', 'category': 'Computational', 'pathogenic': False, 'strength': 'Supporting (B)',
         'description': 'Synonymous variant (silent)', 'threshold': 'ExonicFunc=synonymous'},
        
        # Functional study
        {'code': 'PS3', 'category': 'Functional', 'pathogenic': True, 'strength': 'Strong (P)',
         'description': 'Well-established functional assay shows damaging effect', 'threshold': 'Literature'},
        {'code': 'BS3', 'category': 'Functional', 'pathogenic': False, 'strength': 'Strong (B)',
         'description': 'Well-established functional assay shows normal effect', 'threshold': 'Literature'},
    ]
    
    @staticmethod
    def load_acmg_rules(db: Session, from_csv: str = None):
        """Load ACMG rules from data or CSV file"""
        try:
            if from_csv:
                ACMGRuleLoader._load_from_csv(db, from_csv)
            else:
                ACMGRuleLoader._load_from_data(db)
            logger.info("✓ ACMG rules loaded successfully")
        except Exception as e:
            logger.error(f"Error loading ACMG rules: {str(e)}")
            raise
    
    @staticmethod
    def _load_from_data(db: Session):
        """Load from predefined data"""
        for rule in ACMGRuleLoader.ACMG_RULES_DATA:
            existing = db.query(ACMGRuleMap).filter(
                ACMGRuleMap.criterion_code == rule['code']
            ).first()
            
            if not existing:
                acmg_rule = ACMGRuleMap(
                    criterion_code=rule['code'],
                    criterion_category=rule['category'],
                    pathogenic_direction=rule['pathogenic'],
                    evidence_strength=rule['strength'],
                    description=rule['description'],
                    threshold_logic=rule.get('threshold', ''),
                    vcf_columns='See column_dictionary'
                )
                db.add(acmg_rule)
        
        db.commit()
    
    @staticmethod
    def _load_from_csv(db: Session, csv_path: str):
        """Load from CSV file"""
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get('Code', '').strip()
                if code:
                    existing = db.query(ACMGRuleMap).filter(
                        ACMGRuleMap.criterion_code == code
                    ).first()
                    
                    if not existing:
                        acmg_rule = ACMGRuleMap(
                            criterion_code=code,
                            criterion_category=row.get('Category', ''),
                            evidence_strength=row.get('Strength', ''),
                            description=row.get('Ideal Criteria for Prediction', ''),
                            vcf_columns=row.get('Columns in VCF', ''),
                        )
                        db.add(acmg_rule)
        
        db.commit()


class ColumnDictionaryLoader:
    """Load column dictionary metadata"""
    
    COLUMN_DATA = [
        # Coordinates
        {'name': 'Chr', 'type': 'string', 'domain': 'coordinate',
         'description': 'Chromosome',
         'example': '1, X, MT'},
        {'name': 'Start', 'type': 'integer', 'domain': 'coordinate',
         'description': 'Start position (1-based)',
         'example': '100001'},
        {'name': 'End', 'type': 'integer', 'domain': 'coordinate',
         'description': 'End position (1-based)',
         'example': '100001'},
        {'name': 'Ref', 'type': 'string', 'domain': 'coordinate',
         'description': 'Reference allele',
         'example': 'A, AT'},
        {'name': 'Alt', 'type': 'string', 'domain': 'coordinate',
         'description': 'Alternate allele',
         'example': 'T, A'},
        
        # Identifiers
        {'name': 'dbSNP147', 'type': 'string', 'domain': 'identifier',
         'description': 'dbSNP rsID',
         'example': 'rs1234567'},
        {'name': 'CAID', 'type': 'string', 'domain': 'identifier',
         'description': 'ClinVar Allele ID',
         'example': '123456'},
        
        # Gene annotation
        {'name': 'Gene.refGene', 'type': 'string', 'domain': 'annotation',
         'description': 'Gene symbol from RefGene',
         'example': 'BRCA1'},
        {'name': 'HGVS.c', 'type': 'string', 'domain': 'annotation',
         'description': 'HGVS cDNA nomenclature',
         'example': 'c.68_69delAG'},
        {'name': 'HGVS.p', 'type': 'string', 'domain': 'annotation',
         'description': 'HGVS protein nomenclature',
         'example': 'p.Glu23fs'},
        
        # Functional annotation
        {'name': 'ExonicFunc.refGene', 'type': 'string', 'domain': 'consequence',
         'description': 'Exonic functional consequence',
         'example': 'frameshift insertion, synonymous SNV',
         'acmg_tags': 'PVS1,PM4,BP7'},
        {'name': 'Func.refGene', 'type': 'string', 'domain': 'consequence',
         'description': 'Functional region',
         'example': 'exonic, intronic, splicing',
         'acmg_tags': 'PVS1'},
        
        # Frequencies
        {'name': 'Freq_gnomAD_genome_POPs', 'type': 'float', 'domain': 'frequency',
         'description': 'gnomAD genome allele frequency by population',
         'example': 'AF=0.0001;AF_afr=0.0002',
         'acmg_tags': 'BA1,BS1,PM2'},
        {'name': 'Freq_ESP6500siv2_ALL', 'type': 'float', 'domain': 'frequency',
         'description': 'ESP6500 allele frequency',
         'example': '0.0001'},
        {'name': 'Freq_1000g2015aug_all', 'type': 'float', 'domain': 'frequency',
         'description': '1000 Genomes allele frequency',
         'example': '0.0001'},
        
        # Computational predictions
        {'name': 'CADD_phred', 'type': 'float', 'domain': 'prediction',
         'description': 'CADD PHRED-scaled score',
         'example': '25.3',
         'acmg_tags': 'PP3,BP4'},
        {'name': 'SIFT_score', 'type': 'float', 'domain': 'prediction',
         'description': 'SIFT conservation score',
         'example': '0.02',
         'acmg_tags': 'PP3,BP4'},
        {'name': 'MetaSVM_score', 'type': 'float', 'domain': 'prediction',
         'description': 'MetaSVM prediction score',
         'example': '0.5',
         'acmg_tags': 'PP3,BP4'},
        
        # Conservation
        {'name': 'GERP++_RS', 'type': 'float', 'domain': 'conservation',
         'description': 'GERP++ rejected substitution score',
         'example': '4.5'},
        {'name': 'phyloP46way_placental', 'type': 'float', 'domain': 'conservation',
         'description': 'PhyloP 46-way placental mammals score',
         'example': '2.1'},
        
        # Clinical significance
        {'name': 'clinvar: Clinvar', 'type': 'string', 'domain': 'interpretation',
         'description': 'ClinVar significance classification',
         'example': 'pathogenic, likely_pathogenic, vus',
         'acmg_tags': 'PS1,PM5'},
        {'name': 'InterVar: InterVar and Evidence', 'type': 'string', 'domain': 'interpretation',
         'description': 'InterVar classification with ACMG evidence',
         'example': 'pathogenic(PVS1,PM2)',
         'acmg_tags': 'PVS1,PM2,PP3'},
        
        # Structure/phenotype
        {'name': 'Interpro_domain', 'type': 'string', 'domain': 'structure',
         'description': 'InterPro protein domain annotation',
         'example': 'IPR000001',
         'acmg_tags': 'PM1'},
        {'name': 'rmsk', 'type': 'string', 'domain': 'structure',
         'description': 'RepeatMasker repeat annotation',
         'example': 'Alu, LINE',
         'acmg_tags': 'PM4,BP3'},
        {'name': 'OMIM', 'type': 'string', 'domain': 'phenotype',
         'description': 'OMIM gene entry',
         'example': '113705',
         'acmg_tags': 'BP1'},
    ]
    
    @staticmethod
    def load_column_dictionary(db: Session):
        """Load column dictionary"""
        try:
            for col_data in ColumnDictionaryLoader.COLUMN_DATA:
                existing = db.query(ColumnDictionary).filter(
                    ColumnDictionary.column_name == col_data['name']
                ).first()
                
                if not existing:
                    col_dict = ColumnDictionary(
                        column_name=col_data['name'],
                        column_type=col_data['type'],
                        description=col_data['description'],
                        source_format=col_data['name'],
                        data_domain=col_data['domain'],
                        is_indexed=col_data['domain'] in ['coordinate', 'identifier', 'interpretation'],
                        acmg_rule_tags=col_data.get('acmg_tags', ''),
                        example_values=col_data.get('example', '')
                    )
                    db.add(col_dict)
            
            db.commit()
            logger.info("✓ Column dictionary loaded successfully")
        except Exception as e:
            logger.error(f"Error loading column dictionary: {str(e)}")
            raise


class SourceVersionLoader:
    """Load source database versions"""
    
    SOURCE_DATA = [
        {'name': 'gnomAD', 'version': 'v4.1', 'description': 'Genome Aggregation Database'},
        {'name': 'ClinVar', 'version': 'latest', 'description': 'ClinVar database'},
        {'name': 'RefSeq', 'version': 'GRCh37/hg19', 'description': 'RefSeq annotation'},
        {'name': 'InterVar', 'version': 'latest', 'description': 'InterVar classification'},
        {'name': 'CADD', 'version': 'v1.6', 'description': 'CADD score database'},
        {'name': 'SIFT', 'version': 'latest', 'description': 'SIFT predictions'},
        {'name': 'dbscSNV', 'version': '1.1', 'description': 'splicing score database'},
    ]
    
    @staticmethod
    def load_source_versions(db: Session):
        """Load source versions"""
        try:
            for source in SourceVersionLoader.SOURCE_DATA:
                existing = db.query(SourceVersion).filter(
                    SourceVersion.source_name == source['name']
                ).first()
                
                if not existing:
                    sv = SourceVersion(
                        source_name=source['name'],
                        version_string=source['version'],
                        description=source['description'],
                        is_active=True
                    )
                    db.add(sv)
            
            db.commit()
            logger.info("✓ Source versions loaded successfully")
        except Exception as e:
            logger.error(f"Error loading source versions: {str(e)}")
            raise
