"""
PHASE 2: InterVar Data Loader
Bulk insert normalized variants into database
"""

import logging
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.database import SessionLocal, engine
from app.models import Variant, Gene, ImportLog
from app.ingestion.parser_extended import InterVarParser
from app.ingestion.normalizer_extended import InterVarNormalizer

logger = logging.getLogger(__name__)

class InterVarLoader:
    """
    Load normalized variants into PostgreSQL/SQLite
    Handles gene resolution, FK management, bulk insert
    """
    
    def __init__(self, file_path: str, source_id: str = "intervar_import"):
        self.file_path = file_path
        self.source_id = source_id
        self.parser = InterVarParser(file_path)
        self.db = SessionLocal()
        
        # Statistics
        self.total_rows = 0
        self.inserted_rows = 0
        self.skipped_rows = 0
        self.error_rows = 0
        self.error_details = []
        # In-memory set of variant keys already queued/inserted this run
        self._seen_keys: set = set()
        
        self.start_time = None
        self.end_time = None
    
    def get_or_create_gene(self, gene_symbol: Optional[str], ensembl_id: Optional[str]) -> Optional[int]:
        """
        Get or create gene record
        Returns gene_id or None
        """
        if not gene_symbol and not ensembl_id:
            return None
        
        try:
            # Search by symbol first
            if gene_symbol:
                gene = self.db.query(Gene).filter(
                    Gene.gene_symbol == gene_symbol
                ).first()
                if gene:
                    return gene.gene_id
            
            # Search by ensembl_id
            if ensembl_id:
                gene = self.db.query(Gene).filter(
                    Gene.ensembl_gene_id == ensembl_id
                ).first()
                if gene:
                    return gene.gene_id
            
            # Create new gene
            if gene_symbol:
                new_gene = Gene(
                    gene_symbol=gene_symbol,
                    ensembl_gene_id=ensembl_id
                )
                self.db.add(new_gene)
                self.db.flush()  # Get the ID without committing
                return new_gene.gene_id
        
        except Exception as e:
            logger.warning(f"Error handling gene {gene_symbol}/{ensembl_id}: {str(e)}")
            return None
    
    def normalize_and_validate_row(self, row: Dict[str, Any], row_num: int) -> Optional[Dict[str, Any]]:
        """
        Normalize and validate a row
        Returns normalized row or None if invalid
        """
        try:
            normalized = InterVarNormalizer.normalize_variant_row(row)
            
            if normalized is None:
                self.error_rows += 1
                self.error_details.append(f"Row {row_num}: Failed normalization")
                return None
            
            return normalized
        
        except Exception as e:
            self.error_rows += 1
            self.error_details.append(f"Row {row_num}: {str(e)}")
            logger.error(f"Row {row_num} error: {str(e)}")
            return None
    
    def create_variant_from_normalized(self, normalized: Dict[str, Any]) -> Optional[Variant]:
        """
        Create Variant ORM object from normalized dict
        """
        try:
            # Generate variant key
            variant_key = f"{normalized['Chr']}:{normalized['Start']}:{normalized['Ref']}:{normalized['Alt']}"

            # Skip if already seen in this run (avoids batch-level UNIQUE failures)
            if variant_key in self._seen_keys:
                return None
            self._seen_keys.add(variant_key)
            
            # Get or create gene
            gene_id = self.get_or_create_gene(
                normalized.get('Gene_refGene'),
                normalized.get('Gene_ensGene')
            )
            
            # Create variant
            variant = Variant(
                variant_key=variant_key,
                chromosome=normalized['Chr'],
                start_pos=normalized['Start'],
                end_pos=normalized['End'],
                ref_allele=normalized['Ref'],
                alt_allele=normalized['Alt'],
                rsid=normalized.get('avsnp147'),
                clinvar_allele_id=normalized.get('clinvar_ALLELEID'),
                gene_id=gene_id,
                gene_symbol=normalized.get('Gene_refGene'),
                refseq_gene=normalized.get('Gene_refGene'),
                hgvs_c=normalized.get('AAChange_refGene'),
                hgvs_p=normalized.get('AAChange_refGene'),
                func_region=normalized.get('Func_refGene'),
                exonic_func=normalized.get('ExonicFunc_refGene'),
                transcript_consequence=normalized.get('transcript_consequence'),
                gnomad_af_all=normalized.get('Freq_gnomAD_genome_ALL'),
                gnomad_af_afr=normalized.get('Freq_gnomAD_genome_AFR'),
                gnomad_af_asj=normalized.get('Freq_gnomAD_genome_ASJ'),
                gnomad_af_eas=normalized.get('Freq_gnomAD_genome_EAS'),
                gnomad_af_fin=normalized.get('Freq_gnomAD_genome_FIN'),
                gnomad_af_nfe=normalized.get('Freq_gnomAD_genome_NFE'),
                gnomad_af_oth=normalized.get('Freq_gnomAD_genome_OTH'),
                gnomad_af_amr=normalized.get('Freq_gnomAD_genome_AMR'),
                esp_af=normalized.get('Freq_esp6500siv2_all'),
                kg_af=normalized.get('Freq_1000g2015aug_all'),
                cadd_phred=normalized.get('CADD_phred'),
                cadd_raw=normalized.get('CADD_raw'),
                sift_score=normalized.get('SIFT_score'),
                metasvm_score=normalized.get('MetaSVM_score'),
                dbscsnv_ada_score=normalized.get('dbscSNV_ADA_SCORE'),
                dbscsnv_rf_score=normalized.get('dbscSNV_RF_SCORE'),
                gerp_rs=normalized.get('GERP_RS'),
                phylop46way_placental=normalized.get('phyloP46way_placental'),
                phylop100way_vertebrate=normalized.get('phyloP100way_vertebrate'),
                interpro_domain=normalized.get('Interpro_domain'),
                repeat_masker=normalized.get('rmsk'),
                omim_id=normalized.get('OMIM'),
                clinvar_significance=normalized.get('clinvar_Clinvar'),
                intervar_classification=normalized.get('InterVar'),
                intervar_evidence_text=normalized.get('InterVar'),
                source_file_id=self.source_id,
                raw_row_json=normalized.get('raw_row_json')
            )
            
            return variant
        
        except Exception as e:
            logger.error(f"Error creating variant: {str(e)}")
            return None
    
    def bulk_insert_variants(self, variants: List[Variant]) -> int:
        """
        Bulk insert variants using SQLAlchemy
        Returns count of inserted rows
        """
        if not variants:
            return 0
        
        try:
            # Use bulk_save_objects for efficiency
            self.db.bulk_save_objects(variants, return_defaults=False)
            self.db.commit()
            
            inserted = len(variants)
            self.inserted_rows += inserted
            logger.info(f"✓ Inserted batch of {inserted} variants")
            
            return inserted
        
        except Exception as e:
            self.db.rollback()
            logger.error(f"✗ Bulk insert error: {str(e)}")
            
            # Fallback: insert individually for better error tracking
            inserted = 0
            for variant in variants:
                try:
                    self.db.add(variant)
                    self.db.commit()
                    inserted += 1
                    self.inserted_rows += 1
                except Exception as row_err:
                    self.db.rollback()
                    self.error_rows += 1
                    self.error_details.append(f"Variant {variant.variant_key}: {str(row_err)}")
            
            return inserted
    
    def log_import(self):
        """Log import statistics to import_logs table"""
        try:
            import_log = ImportLog(
                source_file=str(self.file_path),
                file_size=Path(self.file_path).stat().st_size if Path(self.file_path).exists() else 0,
                total_rows=self.total_rows,
                inserted_rows=self.inserted_rows,
                skipped_rows=self.skipped_rows,
                error_rows=self.error_rows,
                import_status="SUCCESS" if self.error_rows == 0 else "PARTIAL",
                error_details=json.dumps(self.error_details[:100]),  # First 100 errors
                import_start=self.start_time,
                import_end=self.end_time
            )
            self.db.add(import_log)
            self.db.commit()
            logger.info(f"✓ Import log created: {self.source_id}")
        except Exception as e:
            logger.error(f"✗ Failed to log import: {str(e)}")
            self.db.rollback()
    
    def ingest_file(self) -> Dict[str, Any]:
        """
        Main ingestion method
        Parse, normalize, and load entire InterVar file
        """
        self.start_time = datetime.now()
        logger.info(f"Starting ingestion of {self.file_path}")
        logger.info(f"Source ID: {self.source_id}")
        
        try:
            total_file_rows = self.parser.get_total_rows()
            logger.info(f"Total rows in file: {total_file_rows}")
            
            batch_count = 0
            for chunk in self.parser.parse_chunks():
                batch_count += 1
                batch_variants = []
                
                for row_num, row in enumerate(chunk, start=1):
                    self.total_rows += 1
                    
                    # Normalize and validate
                    normalized = self.normalize_and_validate_row(row, self.total_rows)
                    if normalized is None:
                        self.skipped_rows += 1
                        continue
                    
                    # Create variant ORM object
                    variant = self.create_variant_from_normalized(normalized)
                    if variant is None:
                        self.skipped_rows += 1
                        continue
                    
                    batch_variants.append(variant)
                
                # Bulk insert batch
                if batch_variants:
                    self.bulk_insert_variants(batch_variants)
                
                # Progress logging every 100k rows
                if self.total_rows % 100000 == 0:
                    elapsed = (datetime.now() - self.start_time).total_seconds()
                    rate = self.total_rows / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"Progress: {self.total_rows:,} rows processed | "
                        f"{rate:.0f} rows/sec | "
                        f"Inserted: {self.inserted_rows:,}, "
                        f"Errors: {self.error_rows:,}"
                    )
            
            self.end_time = datetime.now()
            total_time = (self.end_time - self.start_time).total_seconds()
            
            # Log import
            self.log_import()
            
            # Summary
            summary = {
                "total_rows": self.total_rows,
                "inserted_rows": self.inserted_rows,
                "skipped_rows": self.skipped_rows,
                "error_rows": self.error_rows,
                "elapsed_seconds": total_time,
                "rows_per_second": self.total_rows / total_time if total_time > 0 else 0,
                "status": "SUCCESS" if self.error_rows == 0 else "PARTIAL",
            }
            
            logger.info(f"\n✓ Ingestion complete!")
            logger.info(f"  Total rows: {summary['total_rows']:,}")
            logger.info(f"  Inserted: {summary['inserted_rows']:,}")
            logger.info(f"  Errors: {summary['error_rows']:,}")
            logger.info(f"  Skipped: {summary['skipped_rows']:,}")
            logger.info(f"  Time: {summary['elapsed_seconds']:.1f}s")
            logger.info(f"  Rate: {summary['rows_per_second']:.0f} rows/sec")
            
            return summary
        
        except Exception as e:
            logger.error(f"✗ Ingestion failed: {str(e)}")
            self.end_time = datetime.now()
            raise
        
        finally:
            self.db.close()
