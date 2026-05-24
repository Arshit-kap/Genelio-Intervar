"""
Extended Metadata Loader - Load column dictionary, ACMG rules, and patient metadata
Supports full genomic Q&A engine with ACMG 2015 evidence framework
"""

import logging
from typing import List, Dict, Any
from datetime import datetime
from app.database import SessionLocal
from app.models import (
    ColumnDictionary, ACMGRuleMap, SourceVersion,
    Metadata, Condition
)

logger = logging.getLogger(__name__)

class ExtendedColumnDictionaryLoader:
    """Load comprehensive column dictionary with ACMG triggers"""
    
    COLUMN_DATA = [
        # GENOMIC COORDINATES
        {"name": "Chr", "type": "VARCHAR", "domain": "genomic_coordinate", 
         "semantic_meaning": "Chromosome", "acmg_rules": "multiple", 
         "expected_values": "1-22, X, Y, MT", "display_text": "Chromosome"},
        {"name": "Start", "type": "INTEGER", "domain": "genomic_coordinate",
         "semantic_meaning": "Start position (0-based)", "acmg_rules": "multiple",
         "expected_values": "1-300000000", "display_text": "Start Position"},
        {"name": "End", "type": "INTEGER", "domain": "genomic_coordinate",
         "semantic_meaning": "End position (0-based)", "acmg_rules": "multiple",
         "expected_values": "1-300000000", "display_text": "End Position"},
        {"name": "Ref", "type": "VARCHAR", "domain": "genomic_coordinate",
         "semantic_meaning": "Reference allele", "acmg_rules": "PM4,PM5",
         "expected_values": "A, T, G, C, or indels", "display_text": "Reference Allele"},
        {"name": "Alt", "type": "VARCHAR", "domain": "genomic_coordinate",
         "semantic_meaning": "Alternate allele", "acmg_rules": "PM4,PM5",
         "expected_values": "A, T, G, C, or indels", "display_text": "Alternate Allele"},
        
        # IDENTIFIERS
        {"name": "avsnp147", "type": "VARCHAR", "domain": "identifier",
         "semantic_meaning": "dbSNP rsID", "acmg_rules": "BA1,BS1",
         "expected_values": "rs* format or NULL", "display_text": "dbSNP rsID"},
        {"name": "clinvar_ALLELEID", "type": "VARCHAR", "domain": "identifier",
         "semantic_meaning": "ClinVar Allele ID", "acmg_rules": "PS1",
         "expected_values": "numeric or NULL", "display_text": "ClinVar Allele ID"},
        
        # GENE ANNOTATION
        {"name": "Gene_refGene", "type": "VARCHAR", "domain": "gene_annotation",
         "semantic_meaning": "RefSeq gene symbol", "acmg_rules": "PM1,PM5",
         "expected_values": "HGNC gene symbol or NULL", "display_text": "Gene (RefSeq)"},
        {"name": "Gene_ensGene", "type": "VARCHAR", "domain": "gene_annotation",
         "semantic_meaning": "Ensembl gene ID", "acmg_rules": "PM1,PM5",
         "expected_values": "ENSG* format or NULL", "display_text": "Gene (Ensembl)"},
        {"name": "AAChange_refGene", "type": "VARCHAR", "domain": "gene_annotation",
         "semantic_meaning": "Amino acid change (RefSeq)", "acmg_rules": "PS1,PM1",
         "expected_values": "HGVS p. nomenclature or NULL", "display_text": "AA Change (RefSeq)"},
        {"name": "AAChange_ensGene", "type": "VARCHAR", "domain": "gene_annotation",
         "semantic_meaning": "Amino acid change (Ensembl)", "acmg_rules": "PS1,PM1",
         "expected_values": "HGVS p. nomenclature or NULL", "display_text": "AA Change (Ensembl)"},
        
        # FUNCTIONAL CONSEQUENCE
        {"name": "Func_refGene", "type": "VARCHAR", "domain": "functional",
         "semantic_meaning": "Functional region (RefSeq)", "acmg_rules": "PVS1,PP3",
         "expected_values": "exonic, intronic, ncRNA, etc.", "display_text": "Function (RefSeq)"},
        {"name": "ExonicFunc_refGene", "type": "VARCHAR", "domain": "functional",
         "semantic_meaning": "Exonic function detail (RefSeq)", "acmg_rules": "PVS1,PM4,BP3,BP7",
         "expected_values": "stopgain, frameshift, nonsynonymous SNV, synonymous SNV, etc.",
         "display_text": "Exonic Function (RefSeq)"},
        
        # FREQUENCIES - gnomAD
        {"name": "Freq_gnomAD_genome_ALL", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "gnomAD genome allele frequency (all populations)", "acmg_rules": "BA1,BS1,PM2",
         "expected_values": "0.0-1.0 or NULL", "display_text": "gnomAD AF (All)"},
        {"name": "Freq_gnomAD_genome_AFR", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "gnomAD genome allele frequency (African)", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "gnomAD AF (AFR)"},
        {"name": "Freq_gnomAD_genome_ASJ", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "gnomAD genome allele frequency (Ashkenazi Jewish)", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "gnomAD AF (ASJ)"},
        {"name": "Freq_gnomAD_genome_EAS", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "gnomAD genome allele frequency (East Asian)", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "gnomAD AF (EAS)"},
        {"name": "Freq_gnomAD_genome_FIN", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "gnomAD genome allele frequency (Finnish)", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "gnomAD AF (FIN)"},
        {"name": "Freq_gnomAD_genome_NFE", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "gnomAD genome allele frequency (Non-Finnish European)", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "gnomAD AF (NFE)"},
        {"name": "Freq_gnomAD_genome_OTH", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "gnomAD genome allele frequency (Other)", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "gnomAD AF (OTH)"},
        {"name": "Freq_gnomAD_genome_AMR", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "gnomAD genome allele frequency (Admixed American)", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "gnomAD AF (AMR)"},
        
        # FREQUENCIES - Other
        {"name": "Freq_esp6500siv2_all", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "ESP6500 allele frequency", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "ESP6500 AF"},
        {"name": "Freq_1000g2015aug_all", "type": "FLOAT", "domain": "frequency",
         "semantic_meaning": "1000 Genomes allele frequency", "acmg_rules": "BA1,BS1",
         "expected_values": "0.0-1.0 or NULL", "display_text": "1000G AF"},
        
        # PREDICTION SCORES
        {"name": "CADD_phred", "type": "FLOAT", "domain": "prediction",
         "semantic_meaning": "CADD phred-scaled score (higher=more deleterious)", "acmg_rules": "PP3,BP4",
         "expected_values": "0-40 or NULL", "display_text": "CADD (Phred)"},
        {"name": "CADD_raw", "type": "FLOAT", "domain": "prediction",
         "semantic_meaning": "CADD raw score", "acmg_rules": "PP3,BP4",
         "expected_values": "0-50 or NULL", "display_text": "CADD (Raw)"},
        {"name": "SIFT_score", "type": "FLOAT", "domain": "prediction",
         "semantic_meaning": "SIFT deleterious score (lower=more damaging)", "acmg_rules": "PP3,BP4",
         "expected_values": "0-1 or NULL", "display_text": "SIFT Score"},
        {"name": "MetaSVM_score", "type": "FLOAT", "domain": "prediction",
         "semantic_meaning": "MetaSVM score (higher=more damaging)", "acmg_rules": "PP3,BP4",
         "expected_values": "-2 to 2 or NULL", "display_text": "MetaSVM Score"},
        {"name": "dbscSNV_ADA_SCORE", "type": "FLOAT", "domain": "prediction",
         "semantic_meaning": "dbscSNV ADA score (higher=more deleterious)", "acmg_rules": "BP7",
         "expected_values": "0-1 or NULL", "display_text": "dbscSNV ADA"},
        {"name": "dbscSNV_RF_SCORE", "type": "FLOAT", "domain": "prediction",
         "semantic_meaning": "dbscSNV RF score", "acmg_rules": "BP7",
         "expected_values": "0-1 or NULL", "display_text": "dbscSNV RF"},
        
        # CONSERVATION
        {"name": "GERP_RS", "type": "FLOAT", "domain": "conservation",
         "semantic_meaning": "Gerp++ relative score (higher=more conserved)", "acmg_rules": "PP3",
         "expected_values": "-12 to 7 or NULL", "display_text": "GERP++ RS"},
        {"name": "phyloP46way_placental", "type": "FLOAT", "domain": "conservation",
         "semantic_meaning": "phyloP46way placental score", "acmg_rules": "PP3",
         "expected_values": "-20 to 10 or NULL", "display_text": "phyloP46 (Placental)"},
        {"name": "phyloP100way_vertebrate", "type": "FLOAT", "domain": "conservation",
         "semantic_meaning": "phyloP100way vertebrate score", "acmg_rules": "PP3",
         "expected_values": "-20 to 10 or NULL", "display_text": "phyloP100 (Vertebrate)"},
        
        # STRUCTURE & DOMAIN
        {"name": "Interpro_domain", "type": "VARCHAR", "domain": "structure",
         "semantic_meaning": "InterPro protein domain", "acmg_rules": "PM1",
         "expected_values": "IPR* format or NULL", "display_text": "InterPro Domain"},
        {"name": "rmsk", "type": "VARCHAR", "domain": "structure",
         "semantic_meaning": "RepeatMasker annotation", "acmg_rules": "BP3",
         "expected_values": "repeat family or NULL", "display_text": "Repeat Masker"},
        
        # PHENOTYPE ASSOCIATION
        {"name": "OMIM", "type": "VARCHAR", "domain": "phenotype",
         "semantic_meaning": "OMIM disease ID", "acmg_rules": "PM2,PM1",
         "expected_values": "MIM* format or NULL", "display_text": "OMIM ID"},
        {"name": "Otherinfo", "type": "VARCHAR", "domain": "phenotype",
         "semantic_meaning": "Other phenotype information", "acmg_rules": "PS1,PM1",
         "expected_values": "free text or NULL", "display_text": "Other Info"},
        
        # CLINICAL SIGNIFICANCE
        {"name": "clinvar_Clinvar", "type": "VARCHAR", "domain": "clinical",
         "semantic_meaning": "ClinVar clinical significance", "acmg_rules": "PS1,BP6,BP7",
         "expected_values": "Pathogenic, Likely_pathogenic, VUS, Benign, Likely_benign, Conflicting, Not_provided",
         "display_text": "ClinVar Classification"},
        {"name": "InterVar", "type": "VARCHAR", "domain": "clinical",
         "semantic_meaning": "InterVar classification and evidence codes", "acmg_rules": "multiple",
         "expected_values": "Pathogenic(PVS1,PS1,...), Likely_pathogenic, VUS, etc.",
         "display_text": "InterVar Classification"},
    ]
    
    @staticmethod
    def load_column_dictionary(db):
        """Load all column definitions into database"""
        try:
            for col_data in ExtendedColumnDictionaryLoader.COLUMN_DATA:
                existing = db.query(ColumnDictionary).filter(
                    ColumnDictionary.column_name == col_data["name"]
                ).first()
                
                if not existing:
                    col_dict = ColumnDictionary(
                        column_name=col_data["name"],
                        column_type=col_data["type"],
                        description=col_data["semantic_meaning"],
                        source_format="InterVar TSV",
                        data_domain=col_data["domain"],
                        is_indexed=col_data["domain"] in ["genomic_coordinate", "identifier", "clinical"],
                        acmg_rule_tags=col_data["acmg_rules"],
                        example_values=col_data["expected_values"]
                    )
                    db.add(col_dict)
            
            db.commit()
            logger.info(f"✓ Loaded {len(ExtendedColumnDictionaryLoader.COLUMN_DATA)} column definitions")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to load column dictionary: {str(e)}")
            db.rollback()
            return False


class ExtendedACMGRuleLoader:
    """Load complete ACMG 2015 evidence framework"""
    
    ACMG_RULES = [
        # PATHOGENIC - Very Strong
        {"code": "PVS1", "tier": "Pathogenic_Very_Strong", "category": "Functional",
         "description": "Null variant (nonsense, frameshift, canonical splice sites) in gene where LOF is known mechanism of disease",
         "trigger_columns": "ExonicFunc_refGene IN ('stopgain', 'frameshift insertion', 'frameshift deletion')",
         "participating_columns": "ExonicFunc_refGene,Func_refGene",
         "pathogenic_direction": True},
        
        # PATHOGENIC - Strong
        {"code": "PS1", "tier": "Pathogenic_Strong", "category": "Functional",
         "description": "Same amino acid change as a previously established pathogenic variant regardless of nucleotide change",
         "trigger_columns": "clinvar_Clinvar LIKE '%Pathogenic%' AND AAChange_refGene IS NOT NULL",
         "participating_columns": "clinvar_Clinvar,AAChange_refGene,AAChange_ensGene",
         "pathogenic_direction": True},
        {"code": "PS2", "tier": "Pathogenic_Strong", "category": "Segregation",
         "description": "De novo (both maternity and paternity confirmed) in a child with the disease and no family history",
         "trigger_columns": "Otherinfo LIKE '%de novo%' OR Otherinfo LIKE '%denovo%'",
         "participating_columns": "Otherinfo",
         "pathogenic_direction": True},
        {"code": "PS3", "tier": "Pathogenic_Strong", "category": "Functional",
         "description": "Well-established in vitro or in vivo functional studies supportive of a damaging effect",
         "trigger_columns": "CADD_phred > 30 AND (SIFT_score < 0.01 OR MetaSVM_score > 1.5)",
         "participating_columns": "CADD_phred,SIFT_score,MetaSVM_score",
         "pathogenic_direction": True},
        {"code": "PS4", "tier": "Pathogenic_Strong", "category": "Population",
         "description": "The prevalence of the variant in affected individuals is significantly increased compared to the prevalence in controls",
         "trigger_columns": "Freq_gnomAD_genome_ALL IS NULL OR Freq_gnomAD_genome_ALL < 0.0001",
         "participating_columns": "Freq_gnomAD_genome_ALL,Freq_esp6500siv2_all,Freq_1000g2015aug_all",
         "pathogenic_direction": True},
        
        # PATHOGENIC - Moderate
        {"code": "PM1", "tier": "Pathogenic_Moderate", "category": "Location",
         "description": "Located in a mutational hot spot and/or critical and well-established functional domain without benign variation",
         "trigger_columns": "Interpro_domain IS NOT NULL",
         "participating_columns": "Interpro_domain,AAChange_refGene",
         "pathogenic_direction": True},
        {"code": "PM2", "tier": "Pathogenic_Moderate", "category": "Population",
         "description": "Absent from controls (or at extremely low frequency if recessive) (Table 6)",
         "trigger_columns": "Freq_gnomAD_genome_ALL IS NULL OR Freq_gnomAD_genome_ALL = 0 OR Freq_gnomAD_genome_ALL < 0.0001",
         "participating_columns": "Freq_gnomAD_genome_ALL,Freq_esp6500siv2_all,Freq_1000g2015aug_all",
         "pathogenic_direction": True},
        {"code": "PM4", "tier": "Pathogenic_Moderate", "category": "Functional",
         "description": "Protein altering variant in PREC or other critical residues where different residues have been shown to cause disease",
         "trigger_columns": "ExonicFunc_refGene IN ('nonframeshift insertion', 'nonframeshift deletion', 'nonsynonymous SNV')",
         "participating_columns": "ExonicFunc_refGene,Interpro_domain",
         "pathogenic_direction": True},
        {"code": "PM5", "tier": "Pathogenic_Moderate", "category": "Functional",
         "description": "Novel missense change at an amino acid residue where a different missense change determined to be pathogenic has been seen before",
         "trigger_columns": "ExonicFunc_refGene = 'nonsynonymous SNV' AND CADD_phred > 20",
         "participating_columns": "ExonicFunc_refGene,AAChange_refGene,CADD_phred",
         "pathogenic_direction": True},
        {"code": "PM6", "tier": "Pathogenic_Moderate", "category": "Functional",
         "description": "Assumed de novo, but without confirmed paternity and maternity",
         "trigger_columns": "Otherinfo LIKE '%assumed%de novo%' OR Otherinfo LIKE '%unconfirmed%denovo%'",
         "participating_columns": "Otherinfo",
         "pathogenic_direction": True},
        
        # PATHOGENIC - Supporting
        {"code": "PP1", "tier": "Pathogenic_Supporting", "category": "Segregation",
         "description": "Cosegregation with disease in multiple affected family members in a gene definitively known to cause the disease",
         "trigger_columns": "Otherinfo LIKE '%cosegregation%' OR Otherinfo LIKE '%familial%'",
         "participating_columns": "Otherinfo",
         "pathogenic_direction": True},
        {"code": "PP2", "tier": "Pathogenic_Supporting", "category": "Location",
         "description": "Missense variant in a gene that has a low rate of benign missense variation and in which missense variants are a common mechanism of disease",
         "trigger_columns": "ExonicFunc_refGene = 'nonsynonymous SNV' AND CADD_phred > 15",
         "participating_columns": "ExonicFunc_refGene,CADD_phred",
         "pathogenic_direction": True},
        {"code": "PP3", "tier": "Pathogenic_Supporting", "category": "Computational",
         "description": "Multiple lines of computational evidence support a deleterious effect on the gene or gene product",
         "trigger_columns": "CADD_phred > 20 AND GERP_RS > 2 AND (SIFT_score < 0.05 OR MetaSVM_score > 0.5)",
         "participating_columns": "CADD_phred,CADD_raw,SIFT_score,MetaSVM_score,GERP_RS,phyloP46way_placental",
         "pathogenic_direction": True},
        {"code": "PP4", "tier": "Pathogenic_Supporting", "category": "Segregation",
         "description": "Patient's phenotype or family history is consistent with a single genetic etiology",
         "trigger_columns": "OMIM IS NOT NULL",
         "participating_columns": "OMIM,Otherinfo",
         "pathogenic_direction": True},
        {"code": "PP5", "tier": "Pathogenic_Supporting", "category": "Database",
         "description": "Reputable source recently reports variant as pathogenic, but the evidence is not available to the laboratory to perform an independent evaluation",
         "trigger_columns": "clinvar_Clinvar LIKE '%Likely_pathogenic%'",
         "participating_columns": "clinvar_Clinvar",
         "pathogenic_direction": True},
        
        # BENIGN - Standalone
        {"code": "BA1", "tier": "Benign_Standalone", "category": "Population",
         "description": "Allele frequency is >5% in Exome Sequencing Project, 1000 Genomes Project, or Exome Aggregation Consortium",
         "trigger_columns": "Freq_gnomAD_genome_ALL > 0.05 OR Freq_esp6500siv2_all > 0.05 OR Freq_1000g2015aug_all > 0.05",
         "participating_columns": "Freq_gnomAD_genome_ALL,Freq_esp6500siv2_all,Freq_1000g2015aug_all",
         "pathogenic_direction": False},
        
        # BENIGN - Strong
        {"code": "BS1", "tier": "Benign_Strong", "category": "Population",
         "description": "Allele frequency is greater than expected for disorder (typically >1% in general population)",
         "trigger_columns": "Freq_gnomAD_genome_ALL > 0.01",
         "participating_columns": "Freq_gnomAD_genome_ALL",
         "pathogenic_direction": False},
        {"code": "BS2", "tier": "Benign_Strong", "category": "Segregation",
         "description": "Observed in a healthy adult individual for a recessive (homozygous), X-linked (hemizygous), or mitochondrial (homoplasmic) disorder, with no family history",
         "trigger_columns": "Otherinfo LIKE '%healthy%' AND Otherinfo LIKE '%unaffected%'",
         "participating_columns": "Otherinfo",
         "pathogenic_direction": False},
        {"code": "BS3", "tier": "Benign_Strong", "category": "Functional",
         "description": "Well-established in vitro or in vivo functional studies show no damaging effect",
         "trigger_columns": "CADD_phred < 10 AND SIFT_score > 0.2 AND MetaSVM_score < -0.5",
         "participating_columns": "CADD_phred,SIFT_score,MetaSVM_score",
         "pathogenic_direction": False},
        {"code": "BS4", "tier": "Benign_Strong", "category": "Segregation",
         "description": "Lack of segregation in affected individuals from a large family with a known genetic etiology",
         "trigger_columns": "Otherinfo LIKE '%no segregation%' OR Otherinfo LIKE '%nonsegregating%'",
         "participating_columns": "Otherinfo",
         "pathogenic_direction": False},
        
        # BENIGN - Supporting
        {"code": "BP1", "tier": "Benign_Supporting", "category": "Location",
         "description": "Missense variant in a gene for which primarily truncating variants are known to cause disease",
         "trigger_columns": "ExonicFunc_refGene = 'nonsynonymous SNV' AND Func_refGene = 'exonic'",
         "participating_columns": "ExonicFunc_refGene,Func_refGene",
         "pathogenic_direction": False},
        {"code": "BP2", "tier": "Benign_Supporting", "category": "Location",
         "description": "Observed in trans with a pathogenic variant for a recessive (homozygous) genetic disorder",
         "trigger_columns": "Otherinfo LIKE '%trans%' AND Otherinfo LIKE '%recessive%'",
         "participating_columns": "Otherinfo",
         "pathogenic_direction": False},
        {"code": "BP3", "tier": "Benign_Supporting", "category": "Location",
         "description": "In-frame indels in repetitive regions without a known function",
         "trigger_columns": "ExonicFunc_refGene IN ('nonframeshift insertion', 'nonframeshift deletion') AND rmsk IS NOT NULL",
         "participating_columns": "ExonicFunc_refGene,rmsk",
         "pathogenic_direction": False},
        {"code": "BP4", "tier": "Benign_Supporting", "category": "Computational",
         "description": "Multiple lines of computational evidence suggest no impact on gene or gene product",
         "trigger_columns": "CADD_phred < 15 AND SIFT_score > 0.1 AND MetaSVM_score < 0",
         "participating_columns": "CADD_phred,SIFT_score,MetaSVM_score,GERP_RS",
         "pathogenic_direction": False},
        {"code": "BP5", "tier": "Benign_Supporting", "category": "Location",
         "description": "Variant found in case with an alternate molecular basis for disease",
         "trigger_columns": "Otherinfo LIKE '%alternate%' AND Otherinfo LIKE '%molecular%'",
         "participating_columns": "Otherinfo",
         "pathogenic_direction": False},
        {"code": "BP6", "tier": "Benign_Supporting", "category": "Database",
         "description": "Reputable source recently reports variant as benign, but the evidence is not available to the laboratory to perform an independent evaluation",
         "trigger_columns": "clinvar_Clinvar LIKE '%Benign%' OR clinvar_Clinvar LIKE '%Likely_benign%'",
         "participating_columns": "clinvar_Clinvar",
         "pathogenic_direction": False},
        {"code": "BP7", "tier": "Benign_Supporting", "category": "Functional",
         "description": "Synonymous (silent) variants for which splicing prediction algorithms predict no impact to the splice consensus sequence nor the creation of a new splice site AND the nucleotide is not highly conserved",
         "trigger_columns": "ExonicFunc_refGene = 'synonymous SNV' AND dbscSNV_ADA_SCORE < 0.6 AND dbscSNV_RF_SCORE < 0.6 AND GERP_RS < 2",
         "participating_columns": "ExonicFunc_refGene,dbscSNV_ADA_SCORE,dbscSNV_RF_SCORE,GERP_RS",
         "pathogenic_direction": False},
    ]
    
    @staticmethod
    def load_acmg_rules(db):
        """Load ACMG 2015 evidence codes into database"""
        try:
            for rule_data in ExtendedACMGRuleLoader.ACMG_RULES:
                existing = db.query(ACMGRuleMap).filter(
                    ACMGRuleMap.criterion_code == rule_data["code"]
                ).first()
                
                if not existing:
                    rule_map = ACMGRuleMap(
                        criterion_code=rule_data["code"],
                        criterion_category=rule_data["category"],
                        pathogenic_direction=rule_data["pathogenic_direction"],
                        evidence_strength=rule_data["tier"],
                        description=rule_data["description"],
                        ideal_criteria=rule_data["trigger_columns"],
                        sql_columns=rule_data["participating_columns"],
                        threshold_logic=rule_data["trigger_columns"]
                    )
                    db.add(rule_map)
            
            db.commit()
            logger.info(f"✓ Loaded {len(ExtendedACMGRuleLoader.ACMG_RULES)} ACMG evidence codes")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to load ACMG rules: {str(e)}")
            db.rollback()
            return False


class ExtendedSourceVersionLoader:
    """Load external database versions and APIs"""
    
    SOURCE_DATA = [
        {"source": "gnomAD", "version": "v4.1", "release_date": datetime(2024, 1, 1)},
        {"source": "ClinVar", "version": "2024-05-01", "release_date": datetime(2024, 5, 1)},
        {"source": "RefSeq", "version": "GRCh37/hg19", "release_date": datetime(2024, 1, 1)},
        {"source": "InterVar", "version": "2.2.2", "release_date": datetime(2024, 1, 1)},
        {"source": "CADD", "version": "v1.6", "release_date": datetime(2020, 12, 1)},
        {"source": "SIFT", "version": "2020", "release_date": datetime(2020, 1, 1)},
        {"source": "dbscSNV", "version": "1.1", "release_date": datetime(2016, 2, 1)},
    ]
    
    @staticmethod
    def load_source_versions(db):
        """Load source database versions"""
        try:
            for source_data in ExtendedSourceVersionLoader.SOURCE_DATA:
                existing = db.query(SourceVersion).filter(
                    SourceVersion.source_name == source_data["source"]
                ).first()
                
                if not existing:
                    source = SourceVersion(
                        source_name=source_data["source"],
                        version_string=source_data["version"],
                        release_date=source_data["release_date"],
                        is_active=True
                    )
                    db.add(source)
            
            db.commit()
            logger.info(f"✓ Loaded {len(ExtendedSourceVersionLoader.SOURCE_DATA)} source versions")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to load source versions: {str(e)}")
            db.rollback()
            return False


class PatientMetadataLoader:
    """Load patient demographics and phenotype information"""
    
    @staticmethod
    def load_patient_metadata(db, patient_data: List[Dict[str, Any]]):
        """
        Load patient information into metadata table
        
        Args:
            db: Database session
            patient_data: List of dicts with keys: patient_id, phenotype, age, gender, ethnicity, disease_icd10
        """
        try:
            for patient in patient_data:
                # Store patient info in metadata table with JSON structure
                metadata_key = f"patient_{patient['patient_id']}"
                
                import json
                existing = db.query(Metadata).filter(
                    Metadata.key == metadata_key
                ).first()
                
                if not existing:
                    meta = Metadata(
                        key=metadata_key,
                        value=json.dumps({
                            "patient_id": patient.get("patient_id"),
                            "phenotype": patient.get("phenotype"),
                            "age": patient.get("age"),
                            "gender": patient.get("gender"),
                            "ethnicity": patient.get("ethnicity"),
                            "disease_icd10": patient.get("disease_icd10"),
                            "loaded_date": datetime.now().isoformat()
                        }),
                        data_type="json"
                    )
                    db.add(meta)
            
            db.commit()
            logger.info(f"✓ Loaded {len(patient_data)} patient metadata records")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to load patient metadata: {str(e)}")
            db.rollback()
            return False
