-- PostgreSQL Database Schema for Genomic Variant Interpretation
-- Run migrations instead of executing this directly

-- ============================================
-- 1. GENES TABLE
-- ============================================
CREATE TABLE genes (
    gene_id SERIAL PRIMARY KEY,
    gene_symbol VARCHAR(255) NOT NULL UNIQUE,
    ensembl_gene_id VARCHAR(255),
    refseq_gene VARCHAR(255),
    hgnc_id VARCHAR(255),
    omim_gene_id VARCHAR(255),
    chromosome VARCHAR(5),
    start_pos BIGINT,
    end_pos BIGINT,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_genes_symbol ON genes(gene_symbol);
CREATE INDEX idx_genes_ensembl ON genes(ensembl_gene_id);

-- ============================================
-- 2. CONDITIONS TABLE
-- ============================================
CREATE TABLE conditions (
    condition_id SERIAL PRIMARY KEY,
    condition_label VARCHAR(500) NOT NULL UNIQUE,
    mondo_id VARCHAR(255),
    omim_condition_id VARCHAR(255),
    orpha_number VARCHAR(255),
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_conditions_label ON conditions(condition_label);

-- ============================================
-- 3. VARIANTS TABLE (Main searchable table)
-- ============================================
CREATE TABLE variants (
    variant_id SERIAL PRIMARY KEY,
    variant_key VARCHAR(255) NOT NULL UNIQUE,
    
    -- Genomic coordinates
    chromosome VARCHAR(5) NOT NULL,
    start_pos BIGINT NOT NULL,
    end_pos BIGINT NOT NULL,
    ref_allele VARCHAR(255) NOT NULL,
    alt_allele VARCHAR(255) NOT NULL,
    
    -- Identifiers
    rsid VARCHAR(255),
    caid VARCHAR(255),
    
    -- Gene annotation
    gene_id INTEGER REFERENCES genes(gene_id),
    gene_symbol VARCHAR(255),
    refseq_gene VARCHAR(255),
    hgvs_c VARCHAR(255),
    hgvs_p VARCHAR(255),
    
    -- Functional annotation
    func_region VARCHAR(255),
    exonic_func VARCHAR(255),
    transcript_consequence VARCHAR(255),
    zygosity VARCHAR(50),
    
    -- Frequencies
    gnomad_af_all NUMERIC(10, 8),
    gnomad_af_afr NUMERIC(10, 8),
    gnomad_af_asj NUMERIC(10, 8),
    gnomad_af_eas NUMERIC(10, 8),
    gnomad_af_fin NUMERIC(10, 8),
    gnomad_af_nfe NUMERIC(10, 8),
    gnomad_af_oth NUMERIC(10, 8),
    gnomad_af_amr NUMERIC(10, 8),
    esp_af NUMERIC(10, 8),
    kg_af NUMERIC(10, 8),
    
    -- Predictions
    cadd_phred FLOAT,
    cadd_raw FLOAT,
    sift_score FLOAT,
    sift_pred VARCHAR(50),
    metasvm_score FLOAT,
    metasvm_pred VARCHAR(50),
    dbscsnv_ada_score FLOAT,
    dbscsnv_rf_score FLOAT,
    
    -- Conservation
    gerp_rs FLOAT,
    phylop46way_placental FLOAT,
    phylop100way_vertebrate FLOAT,
    
    -- Structure
    interpro_domain VARCHAR(255),
    repeat_masker VARCHAR(255),
    omim_id VARCHAR(255),
    phenotype_mim VARCHAR(255),
    orpha_number VARCHAR(255),
    orpha_label VARCHAR(255),
    
    -- Clinical significance
    clinvar_significance VARCHAR(255),
    clinvar_allele_id VARCHAR(255),
    intervar_classification VARCHAR(255),
    intervar_evidence_text TEXT,
    
    -- Audit
    source_file_id VARCHAR(255),
    raw_row_json JSONB,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_variants_chr_start ON variants(chromosome, start_pos);
CREATE INDEX idx_variants_key ON variants(variant_key);
CREATE INDEX idx_variants_rsid ON variants(rsid);
CREATE INDEX idx_variants_gene ON variants(gene_symbol);
CREATE INDEX idx_variants_clinvar ON variants(clinvar_significance);
CREATE INDEX idx_variants_intervar ON variants(intervar_classification);
CREATE INDEX idx_variants_exonic ON variants(exonic_func);
CREATE INDEX idx_variants_source ON variants(source_file_id);

-- ============================================
-- 4. VARIANT INTERPRETATIONS
-- ============================================
CREATE TABLE variant_interpretations (
    interpretation_id SERIAL PRIMARY KEY,
    variant_id INTEGER NOT NULL REFERENCES variants(variant_id),
    condition_id INTEGER REFERENCES conditions(condition_id),
    
    source VARCHAR(100) NOT NULL,
    source_record_id VARCHAR(255),
    uuid VARCHAR(36) UNIQUE,
    
    classification VARCHAR(100),
    assertion_method VARCHAR(255),
    schema_label VARCHAR(100),
    content_version_id VARCHAR(100),
    
    produced_at_utc TIMESTAMP WITH TIME ZONE,
    version_note TEXT,
    raw_payload_json JSONB,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_interp_variant ON variant_interpretations(variant_id);
CREATE INDEX idx_interp_condition ON variant_interpretations(condition_id);
CREATE INDEX idx_interp_source ON variant_interpretations(source);
CREATE INDEX idx_interp_class ON variant_interpretations(classification);

-- ============================================
-- 5. EVIDENCE LINES
-- ============================================
CREATE TABLE interpretation_evidence_lines (
    evidence_id SERIAL PRIMARY KEY,
    interpretation_id INTEGER NOT NULL REFERENCES variant_interpretations(interpretation_id),
    
    criterion_code VARCHAR(10),
    evidence_strength VARCHAR(50),
    description TEXT,
    reference_uuid VARCHAR(36),
    
    raw_payload_json JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_evidence_interp ON interpretation_evidence_lines(interpretation_id);
CREATE INDEX idx_evidence_criterion ON interpretation_evidence_lines(criterion_code);

-- ============================================
-- 6. CRITERION ASSESSMENTS
-- ============================================
CREATE TABLE criterion_assessments (
    assessment_id SERIAL PRIMARY KEY,
    interpretation_id INTEGER NOT NULL REFERENCES variant_interpretations(interpretation_id),
    
    criterion_code VARCHAR(10) NOT NULL,
    criterion_category VARCHAR(50),
    evidence_strength VARCHAR(50),
    assertion_method VARCHAR(255),
    description TEXT,
    
    is_met BOOLEAN DEFAULT FALSE,
    score FLOAT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_criteria_interp ON criterion_assessments(interpretation_id);
CREATE INDEX idx_criteria_code ON criterion_assessments(criterion_code);

-- ============================================
-- 7. METADATA TABLES
-- ============================================
CREATE TABLE column_dictionary (
    column_id SERIAL PRIMARY KEY,
    column_name VARCHAR(255) NOT NULL UNIQUE,
    column_type VARCHAR(50),
    description TEXT,
    source_format VARCHAR(100),
    data_domain VARCHAR(50),
    is_indexed BOOLEAN DEFAULT FALSE,
    acmg_rule_tags VARCHAR(255),
    example_values VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_col_dict_name ON column_dictionary(column_name);

-- ============================================
-- ACMG RULE MAP
-- ============================================
CREATE TABLE acmg_rule_map (
    rule_id SERIAL PRIMARY KEY,
    criterion_code VARCHAR(10) NOT NULL UNIQUE,
    criterion_category VARCHAR(50),
    pathogenic_direction BOOLEAN,
    evidence_strength VARCHAR(50),
    description TEXT,
    ideal_criteria TEXT,
    vcf_columns VARCHAR(500),
    threshold_logic VARCHAR(500),
    sql_columns VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_acmg_code ON acmg_rule_map(criterion_code);

-- ============================================
-- 8. OTHER METADATA TABLES
-- ============================================
CREATE TABLE source_versions (
    version_id SERIAL PRIMARY KEY,
    source_name VARCHAR(100) NOT NULL,
    version_string VARCHAR(100) NOT NULL,
    release_date TIMESTAMP WITH TIME ZONE,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE api_cache (
    cache_id SERIAL PRIMARY KEY,
    api_endpoint VARCHAR(500) NOT NULL,
    query_params VARCHAR(1000),
    response_data JSONB,
    status_code INTEGER,
    error_message TEXT,
    cached_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_api_endpoint ON api_cache(api_endpoint);

CREATE TABLE import_logs (
    log_id SERIAL PRIMARY KEY,
    source_file VARCHAR(500) NOT NULL,
    file_size BIGINT,
    total_rows INTEGER,
    inserted_rows INTEGER,
    skipped_rows INTEGER,
    error_rows INTEGER,
    import_status VARCHAR(50),
    error_details TEXT,
    import_start TIMESTAMP WITH TIME ZONE,
    import_end TIMESTAMP WITH TIME ZONE,
    imported_by VARCHAR(100)
);

CREATE INDEX idx_import_file ON import_logs(source_file);
CREATE INDEX idx_import_status ON import_logs(import_status);

CREATE TABLE metadata (
    metadata_id SERIAL PRIMARY KEY,
    key VARCHAR(255) NOT NULL UNIQUE,
    value TEXT,
    data_type VARCHAR(50),
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE query_logs (
    query_id SERIAL PRIMARY KEY,
    query_type VARCHAR(100),
    query_text TEXT,
    variant_filter VARCHAR(500),
    result_count INTEGER,
    execution_time_ms FLOAT,
    executed_by VARCHAR(100),
    executed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_query_type ON query_logs(query_type);
CREATE INDEX idx_query_time ON query_logs(executed_at);
