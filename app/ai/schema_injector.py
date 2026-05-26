"""
Schema Context Injector — Patient Variant DB (34-column InterVar format)
Provides LLM with the exact column dictionary and SQL examples for the
patient_variants.db SQLite database ingested from InterVar txt output.
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SCHEMA = """
DATABASE: patient_variants.db (SQLite)
PRIMARY TABLE: variants
Each row = one variant from the patient's InterVar-annotated WGS report.

IMPORTANT SQL RULES FOR COLUMN NAMES:
  - Columns with dots, spaces, or special chars MUST be double-quoted: "Ref.Gene"
  - Use exactly these quoted forms — do NOT add 'chr' prefix to Chr values
  - '.' values in numeric columns were ingested as NULL — never compare != '.'

══════════════════════════════════════════════════════
COLUMN DICTIONARY
══════════════════════════════════════════════════════

── COORDINATES ──────────────────────────────────────
  Chr           TEXT    — chromosome: 1-22, X, Y, MT  (no 'chr' prefix, e.g. '17' not 'chr17')
  Start         INTEGER — variant start position
  End           INTEGER — variant end position
  Ref           TEXT    — reference allele (A/T/G/C or - for deletion)
  Alt           TEXT    — alternate allele (patient's variant)

── GENE IDENTITY ────────────────────────────────────
  "Ref.Gene"    TEXT    — HGNC gene symbol (e.g. BRCA1, TP53). 'NONE' = intergenic.
                          ALWAYS use: WHERE "Ref.Gene" = 'BRCA1'
  "Gene.ensGene" TEXT   — Ensembl gene symbol (cross-check)

── FUNCTIONAL REGION ────────────────────────────────
  "Func.refGene"  TEXT  — genomic region:
      exonic | splicing | intronic | UTR5 | UTR3 | upstream | downstream | intergenic
      "in coding region" / "exonic" → "Func.refGene" = 'exonic'
      "splice variant"              → "Func.refGene" = 'splicing'

  "ExonicFunc.refGene" TEXT — protein effect (only meaningful when Func.refGene = exonic):
      'synonymous SNV'          — silent, no amino acid change (BP7 candidate)
      'nonsynonymous SNV'       — missense, amino acid changed (use for "missense")
      'stopgain'                — creates premature stop codon (PVS1 candidate)
      'stoploss'                — removes stop codon
      'frameshift deletion'     — shifts reading frame (PVS1 candidate)
      'frameshift insertion'    — shifts reading frame (PVS1 candidate)
      'nonframeshift deletion'  — in-frame deletion (PM4 candidate)
      'nonframeshift insertion' — in-frame insertion (PM4 candidate)
      'unknown'                 — cannot classify
      Natural language mappings:
        "missense"     → "ExonicFunc.refGene" = 'nonsynonymous SNV'
        "synonymous"   → "ExonicFunc.refGene" = 'synonymous SNV'
        "stopgain" / "nonsense" → "ExonicFunc.refGene" = 'stopgain'
        "frameshift"   → "ExonicFunc.refGene" LIKE '%frameshift%'
        "in-frame"     → "ExonicFunc.refGene" IN ('nonframeshift deletion','nonframeshift insertion')

── VARIANT IDENTIFIERS ──────────────────────────────
  avsnp147      TEXT    — dbSNP rsID (e.g. rs201219564). '.' ingested as NULL = novel variant.
  "AAChange.refGene"  TEXT — HGVS protein change (RefSeq), e.g. BRCA1:NM_007294:exon2:c.A68G:p.Q23R
  "AAChange.ensGene"  TEXT — HGVS protein change (Ensembl)
  "AAChange.knownGene" TEXT — HGVS protein change (UCSC knownGene)

── CLINICAL CLASSIFICATION ──────────────────────────
  "clinvar: Clinvar"  TEXT — ClinVar clinical significance:
      'clinvar: Pathogenic' | 'clinvar: Likely_pathogenic' | 'clinvar: Benign'
      'clinvar: Likely_benign' | 'clinvar: Uncertain_significance' | 'clinvar: UNK'
      'clinvar: Conflicting_interpretations_of_pathogenicity'
      Use: "clinvar: Clinvar" LIKE '%Pathogenic%'
      'clinvar: UNK' = not yet submitted to ClinVar (very common — not a problem)

  "InterVar: InterVar and Evidence"  TEXT — ACMG/AMP automated classification + evidence codes:
      Format: 'InterVar: Pathogenic PVS1=1 PS=[0,0,0,0,0] PM=[1,0,...] BA1=0 ...'
      1 = criterion met, 0 = not met.
      ALWAYS use PREFIX LIKE (no leading %):
        Pathogenic        → "InterVar: InterVar and Evidence" LIKE 'InterVar: Pathogenic%'
        Likely pathogenic → "InterVar: InterVar and Evidence" LIKE 'InterVar: Likely pathogenic%'
        Uncertain/VUS     → "InterVar: InterVar and Evidence" LIKE 'InterVar: Uncertain%'
        Likely benign     → "InterVar: InterVar and Evidence" LIKE 'InterVar: Likely benign%'
        Benign            → "InterVar: InterVar and Evidence" LIKE 'InterVar: Benign%'
      ACMG codes: PVS1, PS1-5, PM1-7, PP1-6 (pathogenic); BA1, BS1-5, BP1-8 (benign)

── POPULATION FREQUENCY ─────────────────────────────
  Freq_gnomAD_genome_ALL  REAL — global gnomAD allele frequency (0–1). NULL = not in gnomAD.
      "rare"          → (Freq_gnomAD_genome_ALL IS NULL OR Freq_gnomAD_genome_ALL < 0.01)
      "absent/novel"  → Freq_gnomAD_genome_ALL IS NULL
      "common"        → Freq_gnomAD_genome_ALL > 0.05 (triggers BA1 = Benign)
      "gnomAD > 1%"   → Freq_gnomAD_genome_ALL > 0.01
  Freq_esp6500siv2_all    REAL — ESP6500 frequency (secondary)
  Freq_1000g2015aug_all   REAL — 1000 Genomes frequency (tertiary)
  Freq_gnomAD_genome_POPs TEXT — per-population frequencies: "AFR:x,AMR:x,EAS:x,FIN:x,NFE:x,ASJ:x"

── IN SILICO SCORES ─────────────────────────────────
  CADD_phred       REAL — deleteriousness Phred score: >20 = top 1% most damaging (PP3); <10 = likely benign (BP4)
  CADD_raw         REAL — raw CADD score
  SIFT_score       REAL — <0.05 = damaging (PP3); ≥0.05 = tolerated (BP4). NULL for non-missense.
  MetaSVM_score    REAL — ensemble predictor: >0 = damaging; <0 = tolerated
  "GERP++_RS"      REAL — conservation: >2 = conserved position
  phyloP46way_placental REAL — conservation across 46 mammals: >1 = conserved
  dbscSNV_ADA_SCORE REAL — splicing prediction ADA: >0.6 = likely splice-disrupting
  dbscSNV_RF_SCORE  REAL — splicing prediction RF: >0.6 = likely splice-disrupting

── DISEASE & PHENOTYPE ──────────────────────────────
  OMIM           TEXT — OMIM gene/disease ID (look up at omim.org/entry/{id})
  Phenotype_MIM  TEXT — OMIM phenotype IDs for diseases caused by this gene
  OrphaNumber    TEXT — Orphanet rare disease ID
  Orpha          TEXT — Orphanet disease name (e.g. "Retinitis pigmentosa")

── STRUCTURAL CONTEXT ───────────────────────────────
  Interpro_domain TEXT — protein domain name; NULL = outside known domain
  rmsk            TEXT — RepeatMasker element; NULL = not in repeat region
                         "not in repeat region" → rmsk IS NULL (BP3 for in-frame indels)

── ZYGOSITY ─────────────────────────────────────────
  Otherinfo TEXT — zygosity: 'het' (heterozygous) | 'hom' (homozygous) | 'hemi' (hemizygous)
                   "heterozygous" → Otherinfo = 'het'
                   "homozygous"   → Otherinfo = 'hom'

══════════════════════════════════════════════════════
ACMG EVIDENCE QUICK REFERENCE
══════════════════════════════════════════════════════
  PVS1 = stopgain / frameshift / canonical splice in LOF gene
  PM1  = in functional domain (Interpro_domain IS NOT NULL)
  PM2  = absent from gnomAD (Freq_gnomAD_genome_ALL IS NULL)
  PM4  = in-frame indel ("ExonicFunc.refGene" IN ('nonframeshift deletion','nonframeshift insertion'))
  PP3  = damaging in silico (CADD_phred > 20 OR SIFT_score < 0.05)
  BA1  = gnomAD AF > 5% → Benign (Freq_gnomAD_genome_ALL > 0.05)
  BS1  = gnomAD AF > 1% → Benign Strong
  BP3  = in-frame indel in repeat region (rmsk IS NOT NULL)
  BP4  = benign in silico (CADD_phred < 10)
  BP7  = synonymous + no predicted splice effect
"""

_EXAMPLES = """
EXAMPLE SQL QUERIES — use these exact patterns:

Q: Show all variants in BRCA1
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", "InterVar: InterVar and Evidence", "clinvar: Clinvar", Freq_gnomAD_genome_ALL, CADD_phred FROM variants WHERE "Ref.Gene" = 'BRCA1' LIMIT 50;

Q: Find pathogenic variants
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", "AAChange.refGene", "InterVar: InterVar and Evidence", "clinvar: Clinvar", Freq_gnomAD_genome_ALL FROM variants WHERE "InterVar: InterVar and Evidence" LIKE 'InterVar: Pathogenic%' LIMIT 50;

Q: Show missense variants in TP53
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", "AAChange.refGene", "InterVar: InterVar and Evidence", Freq_gnomAD_genome_ALL, CADD_phred FROM variants WHERE "Ref.Gene" = 'TP53' AND "ExonicFunc.refGene" = 'nonsynonymous SNV' LIMIT 50;

Q: Look up rsID rs201219564
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", avsnp147, "ExonicFunc.refGene", "InterVar: InterVar and Evidence", "clinvar: Clinvar", Freq_gnomAD_genome_ALL FROM variants WHERE avsnp147 = 'rs201219564';

Q: Show heterozygous variants
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", "InterVar: InterVar and Evidence", Otherinfo FROM variants WHERE Otherinfo = 'het' LIMIT 50;

Q: Find rare frameshift mutations (not in gnomAD)
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", "AAChange.refGene", "InterVar: InterVar and Evidence", Freq_gnomAD_genome_ALL, CADD_phred FROM variants WHERE "ExonicFunc.refGene" LIKE '%frameshift%' AND Freq_gnomAD_genome_ALL IS NULL LIMIT 50;

Q: Count variants by classification
SQL: SELECT SUBSTR("InterVar: InterVar and Evidence", 1, 30) AS classification, COUNT(*) AS count FROM variants GROUP BY SUBSTR("InterVar: InterVar and Evidence", 1, 30) ORDER BY count DESC;

Q: Find high-impact variants (CADD > 20, rare)
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", CADD_phred, Freq_gnomAD_genome_ALL, "InterVar: InterVar and Evidence" FROM variants WHERE CADD_phred > 20 AND (Freq_gnomAD_genome_ALL IS NULL OR Freq_gnomAD_genome_ALL < 0.01) ORDER BY CADD_phred DESC LIMIT 50;

Q: Find variants linked to disease in Orphanet
SQL: SELECT DISTINCT "Ref.Gene", Orpha, OrphaNumber, "InterVar: InterVar and Evidence" FROM variants WHERE Orpha LIKE '%retinitis%' AND "Ref.Gene" != 'NONE' LIMIT 20;

Q: Show stopgain variants with gene and protein change
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", "AAChange.refGene", "InterVar: InterVar and Evidence", CADD_phred FROM variants WHERE "ExonicFunc.refGene" = 'stopgain' LIMIT 50;

Q: Variant at chromosome 1 position 69270
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "ExonicFunc.refGene", "AAChange.refGene", avsnp147, "InterVar: InterVar and Evidence", "clinvar: Clinvar", Freq_gnomAD_genome_ALL FROM variants WHERE Chr = '1' AND Start = 69270;

Q: List 50 genes with missense variants
SQL: SELECT DISTINCT "Ref.Gene", COUNT(*) as variant_count FROM variants WHERE "ExonicFunc.refGene" = 'nonsynonymous SNV' AND "Ref.Gene" != 'NONE' GROUP BY "Ref.Gene" ORDER BY variant_count DESC LIMIT 50;

Q: ClinVar Pathogenic variants
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "clinvar: Clinvar", "InterVar: InterVar and Evidence", Freq_gnomAD_genome_ALL, CADD_phred FROM variants WHERE "clinvar: Clinvar" LIKE 'clinvar: Pathogenic%' AND "clinvar: Clinvar" NOT LIKE 'clinvar: Conflicting%' LIMIT 50;

Q: Classification tier breakdown with gene count and variant count per tier
SQL: SELECT CASE WHEN "clinvar: Clinvar" LIKE 'clinvar: Pathogenic%' AND "clinvar: Clinvar" NOT LIKE 'clinvar: Conflicting%' THEN 'Pathogenic' WHEN "clinvar: Clinvar" LIKE 'clinvar: Likely_pathogenic%' THEN 'Likely Pathogenic' WHEN "clinvar: Clinvar" LIKE 'clinvar: Benign%' THEN 'Benign' WHEN "clinvar: Clinvar" LIKE 'clinvar: Uncertain%' OR "clinvar: Clinvar" LIKE 'clinvar: Conflicting%' THEN 'VUS/Conflicting' ELSE 'Other' END AS classification_tier, COUNT(DISTINCT "Ref.Gene") AS gene_count, COUNT(*) AS variant_count FROM variants GROUP BY classification_tier ORDER BY variant_count DESC LIMIT 20;

Q: Splicing variants
SQL: SELECT Chr, Start, Ref, Alt, "Ref.Gene", "Func.refGene", dbscSNV_ADA_SCORE, dbscSNV_RF_SCORE, "InterVar: InterVar and Evidence" FROM variants WHERE "Func.refGene" = 'splicing' LIMIT 50;
"""


def get_schema_context(db: Optional[Session] = None, force_refresh: bool = False) -> str:
    return _SCHEMA


def get_examples() -> str:
    return _EXAMPLES
