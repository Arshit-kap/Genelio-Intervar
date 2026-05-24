-- Sample SQL Queries for Genomic Variant Analysis
-- Use these as reference for common analytical queries

-- ============================================
-- 1. VARIANT SEARCH QUERIES
-- ============================================

-- Find variant by coordinates
SELECT variant_key, gene_symbol, clinvar_significance, gnomad_af_all
FROM variants
WHERE chromosome = '1' AND start_pos >= 100000 AND start_pos <= 200000
ORDER BY start_pos;

-- Find variants in a specific gene
SELECT variant_key, exonic_func, clinvar_significance, cadd_phred
FROM variants
WHERE gene_symbol = 'BRCA1'
ORDER BY cadd_phred DESC;

-- Find by rsID
SELECT variant_key, gene_symbol, clinvar_significance
FROM variants
WHERE rsid = 'rs1234567';

-- ============================================
-- 2. FREQUENCY-BASED QUERIES
-- ============================================

-- High frequency variants (common)
SELECT variant_key, gene_symbol, gnomad_af_all
FROM variants
WHERE gnomad_af_all > 0.01
ORDER BY gnomad_af_all DESC
LIMIT 20;

-- Rare variants (not in gnomAD)
SELECT variant_key, gene_symbol, exonic_func, cadd_phred
FROM variants
WHERE (gnomad_af_all IS NULL OR gnomad_af_all < 0.0001)
  AND exonic_func != 'synonymous'
LIMIT 100;

-- Population-specific frequency comparison
SELECT variant_key, gene_symbol, 
       gnomad_af_all, gnomad_af_afr, gnomad_af_eas, gnomad_af_nfe
FROM variants
WHERE gnomad_af_afr > gnomad_af_nfe * 5  -- 5x enrichment in AFR
  AND gnomad_af_afr > 0.001;

-- ============================================
-- 3. CLINICAL SIGNIFICANCE QUERIES
-- ============================================

-- Pathogenic variants in ClinVar
SELECT variant_key, gene_symbol, clinvar_significance,
       cadd_phred, sift_pred, metasvm_pred
FROM variants
WHERE clinvar_significance LIKE '%pathogenic%'
  AND (cadd_phred > 25 OR sift_pred = 'deleterious')
ORDER BY cadd_phred DESC;

-- VUS (Variants of Uncertain Significance)
SELECT variant_key, gene_symbol, gnomad_af_all,
       cadd_phred, sift_score, metasvm_score
FROM variants
WHERE clinvar_significance LIKE '%uncertain%'
  AND cadd_phred > 20
  AND (sift_score < 0.05 OR metasvm_score > 0.5)
ORDER BY cadd_phred DESC;

-- ============================================
-- 4. FUNCTIONAL CONSEQUENCE QUERIES
-- ============================================

-- Loss-of-function variants (PVS1 candidates)
SELECT variant_key, gene_symbol, exonic_func, gerp_rs, cadd_phred
FROM variants
WHERE exonic_func IN ('frameshift deletion', 'frameshift insertion', 'stopgain')
  AND gerp_rs > 3
ORDER BY gerp_rs DESC;

-- Missense variants with high prediction scores
SELECT variant_key, gene_symbol, exonic_func, cadd_phred, 
       sift_score, metasvm_score, phylop46way_placental
FROM variants
WHERE exonic_func LIKE '%missense%'
  AND cadd_phred > 25
  AND sift_score < 0.05
  AND metasvm_score > 0.5
ORDER BY cadd_phred DESC;

-- Splice site variants
SELECT variant_key, gene_symbol, func_region, cadd_phred,
       dbscsnv_ada_score, dbscsnv_rf_score
FROM variants
WHERE func_region LIKE '%splicing%'
  AND (dbscsnv_ada_score > 0.5 OR dbscsnv_rf_score > 0.5)
ORDER BY cadd_phred DESC;

-- ============================================
-- 5. CONSERVATION QUERIES
-- ============================================

-- Highly conserved variant positions
SELECT variant_key, gene_symbol, exonic_func, 
       gerp_rs, phylop46way_placental, phylop100way_vertebrate
FROM variants
WHERE gerp_rs > 4
  AND phylop46way_placental > 2
ORDER BY gerp_rs DESC;

-- ============================================
-- 6. ACMG EVIDENCE QUERIES
-- ============================================

-- Variants with multiple ACMG criteria
SELECT v.variant_key, v.gene_symbol, 
       COUNT(DISTINCT ca.criterion_code) as criterion_count,
       STRING_AGG(DISTINCT ca.criterion_code, ',') as criteria_codes
FROM variants v
JOIN variant_interpretations vi ON v.variant_id = vi.variant_id
JOIN criterion_assessments ca ON vi.interpretation_id = ca.interpretation_id
WHERE ca.is_met = true
GROUP BY v.variant_id, v.variant_key, v.gene_symbol
HAVING COUNT(DISTINCT ca.criterion_code) >= 2
ORDER BY criterion_count DESC;

-- Pathogenic criteria summary
SELECT 
    ca.criterion_code,
    ca.criterion_category,
    COUNT(*) as count,
    AVG(ca.score) as avg_score
FROM criterion_assessments ca
WHERE ca.is_met = true
GROUP BY ca.criterion_code, ca.criterion_category
ORDER BY count DESC;

-- ============================================
-- 7. GENE-LEVEL AGGREGATION
-- ============================================

-- Variants per gene with statistics
SELECT 
    g.gene_symbol,
    COUNT(DISTINCT v.variant_id) as total_variants,
    COUNT(CASE WHEN v.gnomad_af_all > 0.01 THEN 1 END) as common_count,
    COUNT(CASE WHEN v.gnomad_af_all IS NULL OR v.gnomad_af_all < 0.0001 THEN 1 END) as rare_count,
    COUNT(CASE WHEN v.clinvar_significance LIKE '%pathogenic%' THEN 1 END) as pathogenic_count,
    AVG(v.cadd_phred) as avg_cadd
FROM variants v
LEFT JOIN genes g ON v.gene_id = g.gene_id
WHERE g.gene_symbol IS NOT NULL
GROUP BY g.gene_symbol
HAVING COUNT(DISTINCT v.variant_id) > 10
ORDER BY total_variants DESC;

-- ============================================
-- 8. INTERPRETATION QUERIES
-- ============================================

-- Consensus interpretations
SELECT 
    v.variant_key,
    v.gene_symbol,
    vi.classification,
    COUNT(*) as source_count,
    STRING_AGG(DISTINCT vi.source, ',') as sources
FROM variants v
JOIN variant_interpretations vi ON v.variant_id = vi.variant_id
GROUP BY v.variant_id, v.variant_key, v.gene_symbol, vi.classification
HAVING COUNT(*) > 1
ORDER BY source_count DESC;

-- ============================================
-- 9. QUALITY CONTROL QUERIES
-- ============================================

-- Variants with complete annotation
SELECT COUNT(*) as complete_variants
FROM variants
WHERE chromosome IS NOT NULL
  AND start_pos IS NOT NULL
  AND ref_allele IS NOT NULL
  AND alt_allele IS NOT NULL
  AND gene_symbol IS NOT NULL
  AND clinvar_significance IS NOT NULL;

-- Variants with missing key fields
SELECT 
    COUNT(*) as missing_chromosome
FROM variants WHERE chromosome IS NULL;

SELECT 
    COUNT(*) as missing_gene
FROM variants WHERE gene_symbol IS NULL;

SELECT 
    COUNT(*) as missing_cadd
FROM variants WHERE cadd_phred IS NULL;

-- ============================================
-- 10. IMPORT LOG QUERIES
-- ============================================

-- Import history summary
SELECT 
    DATE(import_start) as date,
    COUNT(*) as import_count,
    SUM(inserted_rows) as total_inserted,
    SUM(error_rows) as total_errors
FROM import_logs
GROUP BY DATE(import_start)
ORDER BY DATE(import_start) DESC;

-- Large imports
SELECT 
    source_file,
    total_rows,
    inserted_rows,
    error_rows,
    ROUND(100.0 * inserted_rows / total_rows, 2) as success_rate,
    import_start
FROM import_logs
ORDER BY total_rows DESC
LIMIT 10;
