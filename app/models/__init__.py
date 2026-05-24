"""
SQLAlchemy ORM Models
Core data models for genomic variant interpretation system
"""

from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, Boolean, ForeignKey, 
    Index, BigInteger, Numeric, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import json

# ============================================
# 1. GENES TABLE - Master gene list
# ============================================
class Gene(Base):
    __tablename__ = "genes"
    
    gene_id = Column(Integer, primary_key=True)
    gene_symbol = Column(String(255), nullable=False, unique=True, index=True)
    ensembl_gene_id = Column(String(255), index=True)
    refseq_gene = Column(String(255), index=True)
    hgnc_id = Column(String(255))
    omim_gene_id = Column(String(255))
    chromosome = Column(String(5))
    start_pos = Column(BigInteger)
    end_pos = Column(BigInteger)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    variants = relationship("Variant", back_populates="gene")
    __table_args__ = (
        Index('idx_genes_symbol', 'gene_symbol'),
        Index('idx_genes_ensembl', 'ensembl_gene_id'),
    )


# ============================================
# 2. CONDITIONS TABLE - Disease/phenotype normalization
# ============================================
class Condition(Base):
    __tablename__ = "conditions"
    
    condition_id = Column(Integer, primary_key=True)
    condition_label = Column(String(500), nullable=False, unique=True, index=True)
    mondo_id = Column(String(255), index=True)
    omim_condition_id = Column(String(255), index=True)
    orpha_number = Column(String(255))
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    interpretations = relationship("VariantInterpretation", back_populates="condition")


# ============================================
# 3. VARIANTS TABLE - Core variant records
# ============================================
class Variant(Base):
    __tablename__ = "variants"
    
    variant_id = Column(Integer, primary_key=True)
    variant_key = Column(String(255), nullable=False, unique=True, index=True)
    
    # Genomic coordinates
    chromosome = Column(String(5), nullable=False, index=True)
    start_pos = Column(BigInteger, nullable=False, index=True)
    end_pos = Column(BigInteger, nullable=False)
    ref_allele = Column(String(255), nullable=False)
    alt_allele = Column(String(255), nullable=False)
    
    # Identifiers
    rsid = Column(String(255), index=True)
    caid = Column(String(255))
    
    # Gene annotation
    gene_id = Column(Integer, ForeignKey('genes.gene_id'))
    gene_symbol = Column(String(255), index=True)
    refseq_gene = Column(String(255))
    hgvs_c = Column(String(255))
    hgvs_p = Column(String(255))
    
    # Functional annotation
    func_region = Column(String(255))
    exonic_func = Column(String(255), index=True)
    transcript_consequence = Column(String(255))
    
    # Zygosity
    zygosity = Column(String(50))
    
    # ==== FREQUENCIES ====
    gnomad_af_all = Column(Numeric(10, 8))
    gnomad_af_afr = Column(Numeric(10, 8))
    gnomad_af_asj = Column(Numeric(10, 8))
    gnomad_af_eas = Column(Numeric(10, 8))
    gnomad_af_fin = Column(Numeric(10, 8))
    gnomad_af_nfe = Column(Numeric(10, 8))
    gnomad_af_oth = Column(Numeric(10, 8))
    gnomad_af_amr = Column(Numeric(10, 8))
    esp_af = Column(Numeric(10, 8))
    kg_af = Column(Numeric(10, 8))
    
    # ==== COMPUTATIONAL PREDICTIONS ====
    cadd_phred = Column(Float)
    cadd_raw = Column(Float)
    sift_score = Column(Float)
    sift_pred = Column(String(50))
    metasvm_score = Column(Float)
    metasvm_pred = Column(String(50))
    dbscsnv_ada_score = Column(Float)
    dbscsnv_rf_score = Column(Float)
    
    # ==== CONSERVATION ====
    gerp_rs = Column(Float)
    phylop46way_placental = Column(Float)
    phylop100way_vertebrate = Column(Float)
    
    # ==== STRUCTURE/DOMAIN ====
    interpro_domain = Column(String(255))
    repeat_masker = Column(String(255))
    omim_id = Column(String(255))
    phenotype_mim = Column(String(255))
    orpha_number = Column(String(255))
    orpha_label = Column(String(255))
    
    # ==== CLINICAL SIGNIFICANCE ====
    clinvar_significance = Column(String(255), index=True)
    clinvar_allele_id = Column(String(255))
    intervar_classification = Column(String(255), index=True)
    intervar_evidence_text = Column(Text)
    
    # ==== AUDIT/PROVENANCE ====
    source_file_id = Column(String(255), index=True)
    raw_row_json = Column(JSON)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    gene = relationship("Gene", back_populates="variants")
    interpretations = relationship("VariantInterpretation", back_populates="variant")
    
    __table_args__ = (
        Index('idx_variants_chr_start', 'chromosome', 'start_pos'),
        Index('idx_variants_key', 'variant_key'),
        Index('idx_variants_rsid', 'rsid'),
        Index('idx_variants_gene', 'gene_symbol'),
        Index('idx_variants_clinvar', 'clinvar_significance'),
        Index('idx_variants_intervar', 'intervar_classification'),
        Index('idx_variants_exonic', 'exonic_func'),
        Index('idx_variants_source', 'source_file_id'),
    )


# ============================================
# 4. VARIANT INTERPRETATIONS TABLE
# ============================================
class VariantInterpretation(Base):
    __tablename__ = "variant_interpretations"
    
    interpretation_id = Column(Integer, primary_key=True)
    variant_id = Column(Integer, ForeignKey('variants.variant_id'), nullable=False, index=True)
    condition_id = Column(Integer, ForeignKey('conditions.condition_id'), index=True)
    
    # Source tracking
    source = Column(String(100), nullable=False, index=True)  # clingen_erepo, intervar, clinvar, internal
    source_record_id = Column(String(255))
    uuid = Column(String(36), unique=True)
    
    # Classification
    classification = Column(String(100), index=True)  # benign, likely_benign, vus, likely_pathogenic, pathogenic
    assertion_method = Column(String(255))
    schema_label = Column(String(100))
    content_version_id = Column(String(100))
    
    # Timestamps
    produced_at_utc = Column(DateTime(timezone=True))
    version_note = Column(Text)
    
    # Raw payload
    raw_payload_json = Column(JSON)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    variant = relationship("Variant", back_populates="interpretations")
    condition = relationship("Condition", back_populates="interpretations")
    evidence_lines = relationship("InterpretationEvidenceLine", back_populates="interpretation")
    criteria = relationship("CriterionAssessment", back_populates="interpretation")
    
    __table_args__ = (
        Index('idx_interp_variant', 'variant_id'),
        Index('idx_interp_condition', 'condition_id'),
        Index('idx_interp_source', 'source'),
        Index('idx_interp_class', 'classification'),
    )


# ============================================
# 5. INTERPRETATION EVIDENCE LINES TABLE
# ============================================
class InterpretationEvidenceLine(Base):
    __tablename__ = "interpretation_evidence_lines"
    
    evidence_id = Column(Integer, primary_key=True)
    interpretation_id = Column(Integer, ForeignKey('variant_interpretations.interpretation_id'), nullable=False, index=True)
    
    # ACMG Criterion
    criterion_code = Column(String(10), index=True)
    evidence_strength = Column(String(50))  # supporting, moderate, strong, very_strong
    description = Column(Text)
    reference_uuid = Column(String(36))
    
    # Raw payload
    raw_payload_json = Column(JSON)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    interpretation = relationship("VariantInterpretation", back_populates="evidence_lines")
    
    __table_args__ = (
        Index('idx_evidence_interp', 'interpretation_id'),
        Index('idx_evidence_criterion', 'criterion_code'),
    )


# ============================================
# 6. ACMG CRITERIA ASSESSMENTS
# ============================================
class CriterionAssessment(Base):
    __tablename__ = "criterion_assessments"
    
    assessment_id = Column(Integer, primary_key=True)
    interpretation_id = Column(Integer, ForeignKey('variant_interpretations.interpretation_id'), nullable=False, index=True)
    
    # Criterion details
    criterion_code = Column(String(10), nullable=False, index=True)
    criterion_category = Column(String(50))  # Population, Computational, Functional, etc.
    evidence_strength = Column(String(50))
    assertion_method = Column(String(255))
    description = Column(Text)
    
    # For ACMG rules - boolean assessments
    is_met = Column(Boolean, default=False)
    score = Column(Float)  # Optional scoring
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    interpretation = relationship("VariantInterpretation", back_populates="criteria")
    
    __table_args__ = (
        Index('idx_criteria_interp', 'interpretation_id'),
        Index('idx_criteria_code', 'criterion_code'),
    )


# ============================================
# 7. EVIDENCE REFERENCES
# ============================================
class EvidenceReference(Base):
    __tablename__ = "evidence_references"
    
    reference_id = Column(Integer, primary_key=True)
    evidence_id = Column(Integer, ForeignKey('interpretation_evidence_lines.evidence_id'), index=True)
    
    # Source information
    source_type = Column(String(50))  # pubmed, clinvar, clingen, etc.
    citation = Column(Text)
    url = Column(String(500))
    doi = Column(String(255))
    pubmed_id = Column(String(255), index=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================
# 8. COLUMN DICTIONARY - Metadata about InterVar columns
# ============================================
class ColumnDictionary(Base):
    __tablename__ = "column_dictionary"
    
    column_id = Column(Integer, primary_key=True)
    column_name = Column(String(255), nullable=False, unique=True, index=True)
    column_type = Column(String(50))  # string, integer, float, categorical, json
    description = Column(Text)
    source_format = Column(String(100))  # raw InterVar column name
    data_domain = Column(String(50))  # coordinate, identifier, frequency, prediction, interpretation, etc.
    is_indexed = Column(Boolean, default=False)
    acmg_rule_tags = Column(String(255))  # CSV of ACMG rules this column supports
    example_values = Column(String(500))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_col_dict_name', 'column_name'),
    )


# ============================================
# 9. ACMG RULE MAP - Mapping of ACMG criteria codes to evidence logic
# ============================================
class ACMGRuleMap(Base):
    __tablename__ = "acmg_rule_map"
    
    rule_id = Column(Integer, primary_key=True)
    criterion_code = Column(String(10), nullable=False, unique=True, index=True)
    criterion_category = Column(String(50))  # Population, Computational, Functional
    pathogenic_direction = Column(Boolean)  # True = pathogenic evidence, False = benign evidence
    evidence_strength = Column(String(50))  # supporting, moderate, strong, very_strong
    
    # Evidence logic
    description = Column(Text)
    ideal_criteria = Column(Text)
    vcf_columns = Column(String(500))  # Relevant VCF columns
    threshold_logic = Column(String(500))  # e.g., "Freq_gnomAD_genome_ALL > 0.05"
    
    # SQL column references in variants table
    sql_columns = Column(String(500))
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_acmg_code', 'criterion_code'),
    )


# ============================================
# 10. SOURCE VERSIONS - Track data source versions
# ============================================
class SourceVersion(Base):
    __tablename__ = "source_versions"
    
    version_id = Column(Integer, primary_key=True)
    source_name = Column(String(100), nullable=False)  # gnomAD, ClinVar, RefSeq, etc.
    version_string = Column(String(100), nullable=False)
    release_date = Column(DateTime)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_source_name', 'source_name'),
    )


# ============================================
# 11. API CACHE - External API caching
# ============================================
class APICache(Base):
    __tablename__ = "api_cache"
    
    cache_id = Column(Integer, primary_key=True)
    api_endpoint = Column(String(500), nullable=False, index=True)
    query_params = Column(String(1000))
    response_data = Column(JSON)
    status_code = Column(Integer)
    error_message = Column(Text)
    cached_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))
    
    __table_args__ = (
        Index('idx_api_endpoint', 'api_endpoint'),
    )


# ============================================
# 12. IMPORT LOGS - Track data imports
# ============================================
class ImportLog(Base):
    __tablename__ = "import_logs"
    
    log_id = Column(Integer, primary_key=True)
    source_file = Column(String(500), nullable=False)
    file_size = Column(BigInteger)
    total_rows = Column(Integer)
    inserted_rows = Column(Integer)
    skipped_rows = Column(Integer)
    error_rows = Column(Integer)
    import_status = Column(String(50))  # success, partial, failed
    error_details = Column(Text)
    import_start = Column(DateTime(timezone=True))
    import_end = Column(DateTime(timezone=True))
    imported_by = Column(String(100))
    
    __table_args__ = (
        Index('idx_import_file', 'source_file'),
        Index('idx_import_status', 'import_status'),
    )


# ============================================
# 13. METADATA - System metadata
# ============================================
class Metadata(Base):
    __tablename__ = "metadata"
    
    metadata_id = Column(Integer, primary_key=True)
    key = Column(String(255), nullable=False, unique=True, index=True)
    value = Column(Text)
    data_type = Column(String(50))  # string, integer, json, etc.
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ============================================
# 14. QUERY LOGS - Track analytical queries
# ============================================
class QueryLog(Base):
    __tablename__ = "query_logs"
    
    query_id = Column(Integer, primary_key=True)
    query_type = Column(String(100))
    query_text = Column(Text)
    variant_filter = Column(String(500))
    result_count = Column(Integer)
    execution_time_ms = Column(Float)
    executed_by = Column(String(100))
    executed_at = Column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        Index('idx_query_type', 'query_type'),
        Index('idx_query_time', 'executed_at'),
    )
