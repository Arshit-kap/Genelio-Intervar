"""
Genomic Variant Interpretation Database Setup
SQLite database for local storage of InterVar, ClinVar, and API metadata
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

class VariantDatabase:
    def __init__(self, db_path='genomic_variants.db'):
        """Initialize database connection"""
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        
    def connect(self):
        """Create database connection"""
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        print(f"✓ Connected to database: {self.db_path}")
        
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            print("✓ Database connection closed")
            
    def create_tables(self):
        """Create all required tables"""
        self.cursor.executescript("""
        -- ============================================
        -- 1. GENES TABLE - Master gene list
        -- ============================================
        CREATE TABLE IF NOT EXISTS genes (
            gene_id INTEGER PRIMARY KEY AUTOINCREMENT,
            gene_symbol TEXT NOT NULL UNIQUE,
            ensembl_gene_id TEXT,
            refseq_gene TEXT,
            hgnc_id TEXT,
            omim_gene_id TEXT,
            chromosome TEXT,
            start_pos BIGINT,
            end_pos BIGINT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- ============================================
        -- 2. CONDITIONS TABLE - Disease/phenotype normalization
        -- ============================================
        CREATE TABLE IF NOT EXISTS conditions (
            condition_id INTEGER PRIMARY KEY AUTOINCREMENT,
            condition_label TEXT NOT NULL UNIQUE,
            mondo_id TEXT,
            omim_condition_id TEXT,
            orpha_number TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- ============================================
        -- 3. VARIANTS TABLE - Core variant records
        -- ============================================
        CREATE TABLE IF NOT EXISTS variants (
            variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
            chromosome TEXT NOT NULL,
            start_pos BIGINT NOT NULL,
            end_pos BIGINT NOT NULL,
            ref_allele TEXT NOT NULL,
            alt_allele TEXT NOT NULL,
            rsid TEXT,
            caid TEXT,
            gene_id INTEGER,
            gene_symbol TEXT,
            refseq_gene TEXT,
            hgvs_c TEXT,
            hgvs_p TEXT,
            func_region TEXT,
            exonic_func TEXT,
            zygosity TEXT,
            
            -- Frequencies
            gnomad_af REAL,
            esp_af REAL,
            kg_af REAL,
            
            -- Predictions
            cadd_phred REAL,
            cadd_raw REAL,
            sift_score REAL,
            metasvm_score REAL,
            dbscsnv_ada_score REAL,
            dbscsnv_rf_score REAL,
            
            -- Conservation
            gerp_rs REAL,
            phylop46way_placental REAL,
            
            -- Structure/phenotype
            interpro_domain TEXT,
            repeat_masker TEXT,
            omim_id TEXT,
            phenotype_mim TEXT,
            orpha_number TEXT,
            orpha_label TEXT,
            
            -- Interpretation data
            clinvar_significance TEXT,
            intervar_classification TEXT,
            intervar_evidence_text TEXT,
            
            -- Audit/provenance
            source_file_id TEXT,
            raw_row_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            FOREIGN KEY (gene_id) REFERENCES genes(gene_id),
            UNIQUE(chromosome, start_pos, end_pos, ref_allele, alt_allele)
        );
        
        -- ============================================
        -- 4. VARIANT INTERPRETATIONS TABLE
        -- ============================================
        CREATE TABLE IF NOT EXISTS variant_interpretations (
            interpretation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            variant_id INTEGER NOT NULL,
            condition_id INTEGER,
            source TEXT NOT NULL,
            source_record_id TEXT,
            uuid TEXT UNIQUE,
            classification TEXT,
            assertion_method TEXT,
            schema_label TEXT,
            content_version_id TEXT,
            produced_at_utc TIMESTAMP,
            version_note TEXT,
            raw_payload_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            FOREIGN KEY (variant_id) REFERENCES variants(variant_id),
            FOREIGN KEY (condition_id) REFERENCES conditions(condition_id),
            UNIQUE(variant_id, condition_id, source)
        );
        
        -- ============================================
        -- 5. EVIDENCE LINES TABLE
        -- ============================================
        CREATE TABLE IF NOT EXISTS interpretation_evidence_lines (
            evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            interpretation_id INTEGER NOT NULL,
            criterion_code TEXT,
            evidence_strength TEXT,
            description TEXT,
            reference_uuid TEXT,
            raw_payload_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            FOREIGN KEY (interpretation_id) REFERENCES variant_interpretations(interpretation_id)
        );
        
        -- ============================================
        -- 6. ACMG CRITERIA ASSESSMENTS
        -- ============================================
        CREATE TABLE IF NOT EXISTS criterion_assessments (
            assessment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            interpretation_id INTEGER NOT NULL,
            criterion_code TEXT NOT NULL,
            criterion_category TEXT,
            evidence_strength TEXT,
            assertion_method TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            FOREIGN KEY (interpretation_id) REFERENCES variant_interpretations(interpretation_id),
            UNIQUE(interpretation_id, criterion_code)
        );
        
        -- ============================================
        -- 7. EVIDENCE REFERENCES
        -- ============================================
        CREATE TABLE IF NOT EXISTS evidence_references (
            reference_id INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id INTEGER,
            source_type TEXT,
            citation TEXT,
            url TEXT,
            doi TEXT,
            pubmed_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            
            FOREIGN KEY (evidence_id) REFERENCES interpretation_evidence_lines(evidence_id)
        );
        
        -- ============================================
        -- 8. API DATA STORAGE
        -- ============================================
        CREATE TABLE IF NOT EXISTS api_cache (
            cache_id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_endpoint TEXT NOT NULL,
            query_params TEXT,
            response_data TEXT NOT NULL,
            status_code INTEGER,
            error_message TEXT,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            
            UNIQUE(api_endpoint, query_params)
        );
        
        -- ============================================
        -- 9. DATA IMPORT LOG
        -- ============================================
        CREATE TABLE IF NOT EXISTS import_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT,
            file_size INTEGER,
            total_rows INTEGER,
            inserted_rows INTEGER,
            skipped_rows INTEGER,
            error_rows INTEGER,
            import_status TEXT,
            error_details TEXT,
            import_start TIMESTAMP,
            import_end TIMESTAMP,
            imported_by TEXT
        );
        
        -- ============================================
        -- 10. METADATA TABLE
        -- ============================================
        CREATE TABLE IF NOT EXISTS metadata (
            metadata_id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT,
            data_type TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        
        self.conn.commit()
        print("✓ All tables created successfully")
        
    def create_indexes(self):
        """Create indexes for better query performance"""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_variants_chr_pos ON variants(chromosome, start_pos, end_pos)",
            "CREATE INDEX IF NOT EXISTS idx_variants_rsid ON variants(rsid)",
            "CREATE INDEX IF NOT EXISTS idx_variants_gene ON variants(gene_symbol)",
            "CREATE INDEX IF NOT EXISTS idx_variants_clinvar ON variants(clinvar_significance)",
            "CREATE INDEX IF NOT EXISTS idx_variants_intervar ON variants(intervar_classification)",
            "CREATE INDEX IF NOT EXISTS idx_interpretations_variant ON variant_interpretations(variant_id)",
            "CREATE INDEX IF NOT EXISTS idx_interpretations_condition ON variant_interpretations(condition_id)",
            "CREATE INDEX IF NOT EXISTS idx_interpretations_source ON variant_interpretations(source)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_interpretation ON interpretation_evidence_lines(interpretation_id)",
            "CREATE INDEX IF NOT EXISTS idx_api_cache_endpoint ON api_cache(api_endpoint)",
            "CREATE INDEX IF NOT EXISTS idx_criteria_interpretation ON criterion_assessments(interpretation_id)",
        ]
        
        for index in indexes:
            self.cursor.execute(index)
        
        self.conn.commit()
        print(f"✓ Created {len(indexes)} indexes")
        
    def init_metadata(self):
        """Initialize metadata table with system information"""
        metadata = [
            ('database_version', '1.0', 'string', 'Database schema version'),
            ('created_date', datetime.now().isoformat(), 'datetime', 'Database creation date'),
            ('last_updated', datetime.now().isoformat(), 'datetime', 'Last database update'),
            ('gnomad_version', 'v4.1', 'string', 'gnomAD database version'),
            ('clinvar_version', 'latest', 'string', 'ClinVar database version'),
            ('intervar_version', 'latest', 'string', 'InterVar classification version'),
            ('refseq_version', 'latest', 'string', 'RefSeq reference genome version'),
        ]
        
        for key, value, data_type, description in metadata:
            self.cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value, data_type, description)
                VALUES (?, ?, ?, ?)
            """, (key, value, data_type, description))
        
        self.conn.commit()
        print("✓ Metadata initialized")
        
    def setup_database(self):
        """Complete database setup"""
        self.connect()
        self.create_tables()
        self.create_indexes()
        self.init_metadata()
        print("\n✓ Database setup complete!")
        return self


if __name__ == "__main__":
    # Initialize database
    db = VariantDatabase('c:\\Users\\Admin\\Desktop\\intervar\\genomic_variants.db')
    db.setup_database()
    db.close()
