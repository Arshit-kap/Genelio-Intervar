"""
Main Ingestion Pipeline
Reads InterVar files, processes, and inserts into database
"""

import csv
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import (
    Variant, Gene, VariantInterpretation, ImportLog,
    InterpretationEvidenceLine, CriterionAssessment
)
from app.ingestion.parser import VariantParser, VariantValidator
from app.config import BATCH_SIZE, VALIDATE_ON_INSERT

logger = logging.getLogger(__name__)

class VariantIngestionPipeline:
    """Main ingestion pipeline for variant data"""
    
    def __init__(self, db: Session):
        self.db = db
        self.parser = VariantParser()
        self.validator = VariantValidator()
        self.import_stats = {
            'total_rows': 0,
            'inserted_rows': 0,
            'skipped_rows': 0,
            'error_rows': 0,
            'errors': []
        }
    
    def ingest_file(self, file_path: str, source_file_id: str = None) -> Dict[str, Any]:
        """
        Ingest an InterVar TSV/CSV file
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if source_file_id is None:
            source_file_id = file_path.name
        
        import_start = datetime.utcnow()
        
        logger.info(f"Starting ingestion of {file_path}")
        
        # Detect delimiter
        delimiter = self._detect_delimiter(file_path)
        
        # Read and process file
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            
            if not reader.fieldnames:
                raise ValueError("Empty file or invalid format")
            
            logger.info(f"Detected columns: {reader.fieldnames}")
            
            batch = []
            for row_num, row in enumerate(reader, start=2):  # start=2 because row 1 is header
                self.import_stats['total_rows'] += 1
                
                try:
                    # Validate
                    if VALIDATE_ON_INSERT:
                        is_valid, error = self.validator.validate_row(row)
                        if not is_valid:
                            logger.warning(f"Row {row_num} validation failed: {error}")
                            self.import_stats['skipped_rows'] += 1
                            self.import_stats['errors'].append({
                                'row': row_num,
                                'error': error
                            })
                            continue
                    
                    # Parse row
                    variant = self._parse_variant_row(row, source_file_id)
                    
                    batch.append(variant)
                    
                    # Insert batch
                    if len(batch) >= BATCH_SIZE:
                        self._insert_batch(batch)
                        self.import_stats['inserted_rows'] += len(batch)
                        batch = []
                
                except Exception as e:
                    logger.error(f"Error processing row {row_num}: {str(e)}")
                    self.import_stats['error_rows'] += 1
                    self.import_stats['errors'].append({
                        'row': row_num,
                        'error': str(e)
                    })
                    continue
            
            # Insert remaining batch
            if batch:
                self._insert_batch(batch)
                self.import_stats['inserted_rows'] += len(batch)
        
        import_end = datetime.utcnow()
        
        # Log import
        self._log_import(file_path, import_start, import_end, source_file_id)
        
        logger.info(f"Ingestion complete. Inserted: {self.import_stats['inserted_rows']}, "
                   f"Skipped: {self.import_stats['skipped_rows']}, "
                   f"Errors: {self.import_stats['error_rows']}")
        
        return self.import_stats
    
    def _detect_delimiter(self, file_path: Path) -> str:
        """Detect file delimiter (tab or comma)"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            first_line = f.readline()
            if '\t' in first_line:
                return '\t'
            return ','
    
    def _parse_variant_row(self, row: Dict[str, Any], source_file_id: str) -> Variant:
        """Parse a single variant row into Variant model"""
        
        # Genomic coordinates
        chromosome = self.parser.safe_string(row.get('Chr', ''))
        if chromosome and chromosome.lower().startswith('chr'):
            chromosome = chromosome[3:]
        
        start_pos = self.parser.safe_int(row.get('Start', row.get('start')))
        end_pos = self.parser.safe_int(row.get('End', row.get('end')))
        ref_allele = self.parser.safe_string(row.get('Ref', '')).upper() if row.get('Ref') else ''
        alt_allele = self.parser.safe_string(row.get('Alt', '')).upper() if row.get('Alt') else ''
        
        # Generate variant key
        variant_key = self.parser.generate_variant_key(chromosome, start_pos, ref_allele, alt_allele)
        
        # Gene info
        gene_symbol = self.parser.safe_string(row.get('Gene.refGene', row.get('gene_symbol')))
        
        # Get or create gene
        gene = None
        if gene_symbol:
            gene = self.db.query(Gene).filter(Gene.gene_symbol == gene_symbol).first()
            if not gene:
                gene = Gene(gene_symbol=gene_symbol)
                self.db.add(gene)
                self.db.flush()
        
        # Frequencies
        freq_str = row.get('Freq_gnomAD_genome_POPs', row.get('Freq_gnomAD_POPs', ''))
        frequencies = self.parser.parse_gnomad_frequencies(freq_str)
        
        # Computational scores
        scores = self.parser.parse_computational_scores(row)
        
        # Conservation scores
        conservation = self.parser.parse_conservation_scores(row)
        
        # InterVar evidence
        intervar_str = row.get('InterVar', '')
        acmg_evidence = self.parser.parse_intervar_evidence(intervar_str)
        
        # Transcript consequence
        exonic_func = self.parser.safe_string(row.get('ExonicFunc.refGene', row.get('ExonicFunc')))
        func_region = self.parser.safe_string(row.get('Func.refGene', row.get('Func')))
        transcript_consequence = self.parser.parse_transcript_consequence(exonic_func, func_region)
        
        # Create variant
        variant = Variant(
            variant_key=variant_key,
            chromosome=chromosome,
            start_pos=start_pos,
            end_pos=end_pos,
            ref_allele=ref_allele,
            alt_allele=alt_allele,
            rsid=self.parser.safe_string(row.get('dbSNP147', row.get('rsID'))),
            caid=self.parser.safe_string(row.get('CAID')),
            gene_id=gene.gene_id if gene else None,
            gene_symbol=gene_symbol,
            refseq_gene=self.parser.safe_string(row.get('Gene.refGene')),
            hgvs_c=self.parser.safe_string(row.get('HGVS.c')),
            hgvs_p=self.parser.safe_string(row.get('HGVS.p')),
            func_region=func_region,
            exonic_func=exonic_func,
            transcript_consequence=transcript_consequence,
            zygosity=self.parser.safe_string(row.get('Zygosity')),
            
            # Frequencies
            gnomad_af_all=frequencies['gnomad_af_all'],
            gnomad_af_afr=frequencies['gnomad_af_afr'],
            gnomad_af_asj=frequencies['gnomad_af_asj'],
            gnomad_af_eas=frequencies['gnomad_af_eas'],
            gnomad_af_fin=frequencies['gnomad_af_fin'],
            gnomad_af_nfe=frequencies['gnomad_af_nfe'],
            gnomad_af_oth=frequencies['gnomad_af_oth'],
            gnomad_af_amr=frequencies['gnomad_af_amr'],
            esp_af=self.parser.safe_numeric(row.get('Freq_ESP6500siv2_ALL')),
            kg_af=self.parser.safe_numeric(row.get('Freq_1000g2015aug_all')),
            
            # Scores
            cadd_phred=scores['cadd_phred'],
            cadd_raw=scores['cadd_raw'],
            sift_score=scores['sift_score'],
            sift_pred=scores['sift_pred'],
            metasvm_score=scores['metasvm_score'],
            metasvm_pred=scores['metasvm_pred'],
            dbscsnv_ada_score=scores['dbscsnv_ada_score'],
            dbscsnv_rf_score=scores['dbscsnv_rf_score'],
            
            # Conservation
            gerp_rs=conservation['gerp_rs'],
            phylop46way_placental=conservation['phylop46way_placental'],
            phylop100way_vertebrate=conservation['phylop100way_vertebrate'],
            
            # Structure
            interpro_domain=self.parser.safe_string(row.get('Interpro_domain')),
            repeat_masker=self.parser.safe_string(row.get('rmsk', row.get('RepeatMasker'))),
            omim_id=self.parser.safe_string(row.get('OMIM')),
            phenotype_mim=self.parser.safe_string(row.get('Phenotype_MIM')),
            orpha_number=self.parser.safe_string(row.get('Orpha')),
            
            # Clinical significance
            clinvar_significance=self.parser.safe_string(row.get('clinvar: Clinvar')),
            clinvar_allele_id=self.parser.safe_string(row.get('ClinVar_CLNALID')),
            intervar_classification=self.parser.safe_string(row.get('InterVar', '')),
            intervar_evidence_text=intervar_str,
            
            # Audit
            source_file_id=source_file_id,
            raw_row_json=dict(row)
        )
        
        return variant
    
    def _insert_batch(self, batch: List[Variant]):
        """Insert a batch of variants"""
        try:
            self.db.bulk_save_objects(batch, return_defaults=False)
            self.db.commit()
            logger.debug(f"Inserted batch of {len(batch)} variants")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error inserting batch: {str(e)}")
            raise
    
    def _log_import(self, file_path: Path, import_start: datetime, import_end: datetime, source_file_id: str):
        """Log import operation"""
        try:
            log = ImportLog(
                source_file=str(file_path),
                file_size=file_path.stat().st_size,
                total_rows=self.import_stats['total_rows'],
                inserted_rows=self.import_stats['inserted_rows'],
                skipped_rows=self.import_stats['skipped_rows'],
                error_rows=self.import_stats['error_rows'],
                import_status='success' if self.import_stats['error_rows'] == 0 else 'partial',
                error_details=str(self.import_stats['errors'][:10]),  # First 10 errors
                import_start=import_start,
                import_end=import_end,
                imported_by='automated'
            )
            self.db.add(log)
            self.db.commit()
        except Exception as e:
            logger.error(f"Error logging import: {str(e)}")
