"""Tests for the rewritten agentic pipeline.

Covers the worked example from §"Example Conversation" of the multi-API
integration spec, plus regression checks on the combined ClinVar +
InterVar pathogenicity rule (spec §3) and the local-first inheritance /
disease readers (spec §5).

These tests deliberately do NOT spin up Django's view layer or the
vLLM client. The category renderers are deterministic; we feed them
synthetic ``parsed_data`` rows that mimic the BioAro CSV shape used
by ``reports.analyzers.clinical_csv``.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from chatbot.agentic import (
    body_systems,
    categories,
    local_context as lc,
    tools,
)
from chatbot.agentic.tools import Bucket


def _row(**overrides):
    """Build a minimal raw_row dict — only overrides are set."""
    base = {
        "Ref.Gene": None,
        "AAChange.refGene": None,
        "Func.refGene": "exonic",
        "ExonicFunc.refGene": "nonsynonymous SNV",
        "clinvar: Clinvar": None,
        "ClinVar_Disease": None,
        "ClinVar_Review_Status": None,
        "ClinVar_Stars": None,
        "InterVar: InterVar and Evidence": None,
        "Zygosity": None,
        "Mode_of_Inheritance": None,
        "CADD_phred": None,
        "REVEL_score": None,
        "PVS1": None,
        "PM2": None,
        "PP3": None,
        "BA1": None,
        "Primary_Finding": None,
        "Secondary_Finding": None,
        "Carrier_Status": None,
        "Actionable": None,
        "OMIM_ID": None,
        "HPO_ID": None,
        "MONDO_ID": None,
        "Freq_gnomAD_genome_ALL": None,
        "Freq_esp6500siv2_all": None,
        "Freq_1000g2015aug_all": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Pathogenicity rule — combined ClinVar + InterVar (spec §3)
# ---------------------------------------------------------------------------

class PathogenicityBucketTests(SimpleTestCase):
    """Each branch of the spec §3 decision tree should produce the
    expected bucket. These are the most-cited bioinformatician failure
    modes from the previous build."""

    def test_both_agree_pathogenic_is_strong(self):
        row = _row(
            **{"clinvar: Clinvar": "Pathogenic",
               "InterVar: InterVar and Evidence": "Pathogenic PVS1=1"}
        )
        self.assertEqual(tools.pathogenicity_bucket(row), Bucket.STRONG_PATHOGENIC)

    def test_clinvar_only_pathogenic(self):
        row = _row(
            **{"clinvar: Clinvar": "Pathogenic",
               "InterVar: InterVar and Evidence": "Uncertain significance"}
        )
        self.assertEqual(tools.pathogenicity_bucket(row), Bucket.CLINVAR_PATHOGENIC)

    def test_intervar_only_pathogenic(self):
        row = _row(
            **{"clinvar: Clinvar": "Uncertain_significance",
               "InterVar: InterVar and Evidence": "Pathogenic PVS1=1"}
        )
        self.assertEqual(tools.pathogenicity_bucket(row), Bucket.INTERVAR_PATHOGENIC)

    def test_conflicting_clinvar_with_intervar_pathogenic(self):
        row = _row(
            **{"clinvar: Clinvar":
                "Conflicting_classifications_of_pathogenicity",
               "InterVar: InterVar and Evidence": "Pathogenic"}
        )
        self.assertEqual(
            tools.pathogenicity_bucket(row), Bucket.CONFLICTING_PATHOGENIC,
        )

    def test_both_vus_with_high_cadd_and_revel_and_rare_is_surfaced(self):
        row = _row(
            **{"clinvar: Clinvar": "Uncertain_significance",
               "InterVar: InterVar and Evidence": "Uncertain significance",
               "CADD_phred": "24",
               "REVEL_score": "0.6",
               "Freq_gnomAD_genome_ALL": "0.0005"}
        )
        self.assertEqual(tools.pathogenicity_bucket(row), Bucket.VUS_HIGH_SCORE)

    def test_both_vus_with_low_revel_is_dropped(self):
        row = _row(
            **{"clinvar: Clinvar": "Uncertain_significance",
               "InterVar: InterVar and Evidence": "Uncertain significance",
               "CADD_phred": "24",
               "REVEL_score": "0.2",
               "Freq_gnomAD_genome_ALL": "0.0005"}
        )
        self.assertEqual(tools.pathogenicity_bucket(row), Bucket.DROP)

    def test_vus_high_score_but_common_is_dropped(self):
        row = _row(
            **{"clinvar: Clinvar": "Uncertain_significance",
               "InterVar: InterVar and Evidence": "Uncertain significance",
               "CADD_phred": "24",
               "REVEL_score": "0.6",
               "Freq_gnomAD_genome_ALL": "0.05"}  # 5%, not rare
        )
        self.assertEqual(tools.pathogenicity_bucket(row), Bucket.DROP)

    def test_benign_with_high_freq_is_dropped(self):
        row = _row(
            **{"clinvar: Clinvar": "Benign",
               "Freq_gnomAD_genome_ALL": "0.20"}
        )
        self.assertEqual(tools.pathogenicity_bucket(row), Bucket.DROP)

    def test_priority_shim_returns_none_for_drop(self):
        row = _row(**{"clinvar: Clinvar": "Benign",
                      "Freq_gnomAD_genome_ALL": "0.20"})
        self.assertIsNone(tools.pathogenicity_priority(row))

    def test_priority_shim_returns_numeric_for_strong(self):
        row = _row(**{"clinvar: Clinvar": "Pathogenic",
                      "InterVar: InterVar and Evidence": "Pathogenic"})
        self.assertEqual(tools.pathogenicity_priority(row), 1)


# ---------------------------------------------------------------------------
# Local-first context readers
# ---------------------------------------------------------------------------

class LocalContextTests(SimpleTestCase):
    def test_inheritance_canonicalisation(self):
        self.assertEqual(
            lc.inheritance_pattern(_row(Mode_of_Inheritance="AR")),
            "Autosomal Recessive",
        )
        self.assertEqual(
            lc.inheritance_pattern(_row(Mode_of_Inheritance="autosomal_dominant")),
            "Autosomal Dominant",
        )
        self.assertEqual(
            lc.inheritance_pattern(_row(Mode_of_Inheritance="XLR")),
            "X-Linked Recessive",
        )
        self.assertIsNone(lc.inheritance_pattern(_row()))

    def test_omim_ids_splits_pipe_joined(self):
        self.assertEqual(
            lc.omim_ids(_row(OMIM_ID="219700|602421")),
            ["219700", "602421"],
        )
        self.assertEqual(lc.omim_ids(_row()), [])

    def test_hpo_ids_splits_and_drops_sentinels(self):
        self.assertEqual(
            lc.hpo_ids(_row(HPO_ID="HP:0006528|HP:0002099|.")),
            ["HP:0006528", "HP:0002099"],
        )

    def test_zygosity_normalisation(self):
        self.assertEqual(lc.zygosity(_row(Zygosity="Heterozygous")), "Heterozygous")
        self.assertEqual(lc.zygosity(_row(Zygosity="hemi")), "Hemizygous")
        self.assertIsNone(lc.zygosity(_row(Zygosity=".")))

    def test_reproductive_paragraph_AR_het_says_carrier(self):
        para = lc.reproductive_paragraph("Autosomal Recessive", "Heterozygous")
        self.assertIsNotNone(para)
        self.assertIn("carrier", para.lower())
        self.assertIn("25%", para)

    def test_reproductive_paragraph_AD_het_says_50_percent(self):
        para = lc.reproductive_paragraph("Autosomal Dominant", "Heterozygous")
        self.assertIsNotNone(para)
        self.assertIn("50%", para)


# ---------------------------------------------------------------------------
# Body-system router
# ---------------------------------------------------------------------------

class BodySystemTests(SimpleTestCase):
    def test_lungs_resolves_to_respiratory_root(self):
        m = body_systems.resolve_body_system("lungs")
        self.assertIsNotNone(m)
        self.assertEqual(m.body_system.root_hpo_id, "HP:0002086")

    def test_heart_resolves_to_cardiovascular_root(self):
        m = body_systems.resolve_body_system("heart")
        self.assertIsNotNone(m)
        self.assertEqual(m.body_system.root_hpo_id, "HP:0001626")

    def test_unrecognised_term_returns_none(self):
        m = body_systems.resolve_body_system("weather")
        self.assertIsNone(m)


# ---------------------------------------------------------------------------
# Category renderers
# ---------------------------------------------------------------------------

# Rows that mirror the PDF's example conversation
# ("Do I have anything that could affect my lungs?") — used by C1 + E1 tests.
_CFTR_F508DEL = _row(**{
    "Ref.Gene": "CFTR",
    "AAChange.refGene": "CFTR:NM_000492:exon11:c.1521_1523delCTT:p.F508del",
    "clinvar: Clinvar": "Pathogenic",
    "ClinVar_Disease": "Cystic fibrosis",
    "ClinVar_Stars": "4",
    "InterVar: InterVar and Evidence": "Pathogenic PVS1=0, PS3=1, PM1=1",
    "PVS1": "NO",
    "Zygosity": "Heterozygous",
    "Mode_of_Inheritance": "Autosomal Recessive",
    "OMIM_ID": "219700",
    "HPO_ID": "HP:0006528|HP:0002099",
    "Carrier_Status": "YES",
})
_SFTPC_I73T = _row(**{
    "Ref.Gene": "SFTPC",
    "AAChange.refGene": "SFTPC:NM_003018:exon3:c.218T>C:p.I73T",
    "clinvar: Clinvar": "Uncertain_significance",
    "ClinVar_Disease": "Interstitial lung disease",
    "InterVar: InterVar and Evidence": "Uncertain significance",
    "CADD_phred": "24",
    "REVEL_score": "0.62",
    "Freq_gnomAD_genome_ALL": "0.0001",
    "Zygosity": "Heterozygous",
})


class CategoryC1Tests(SimpleTestCase):
    """The PDF's worked example — a body-system query for lungs."""

    def test_renders_strong_and_vus_buckets_distinctly(self):
        pairs = [
            (_CFTR_F508DEL, Bucket.STRONG_PATHOGENIC),
            (_SFTPC_I73T, Bucket.VUS_HIGH_SCORE),
        ]
        out = categories.render_C1(
            body_system_name="Respiratory system",
            hpo_root_id="HP:0002086",
            matched_with_buckets=pairs,
            universe=80,
        )
        self.assertIn("CFTR", out)
        self.assertIn("p.F508del", out)
        self.assertIn("SFTPC", out)
        self.assertIn("Respiratory system", out)
        # Both bucket labels rendered
        self.assertIn("Strong evidence", out)
        self.assertIn("Uncertain significance", out)
        # Carrier paragraph (AR + het) surfaced for CFTR
        self.assertIn("carrier", out.lower())
        # Disclaimer present
        self.assertIn("counselor", out.lower())


class CategoryA1Tests(SimpleTestCase):
    def test_splits_strong_from_one_sided(self):
        pairs = [
            (_CFTR_F508DEL, Bucket.STRONG_PATHOGENIC),
            (_SFTPC_I73T, Bucket.VUS_HIGH_SCORE),
        ]
        out = categories.render_A1(pairs, universe=80)
        # Strong section
        self.assertIn("both ClinVar and InterVar", out)
        # One-sided section
        self.assertIn("flagged by one source", out)
        self.assertIn("CFTR", out)
        self.assertIn("SFTPC", out)


class CategoryE1Tests(SimpleTestCase):
    def test_renders_AR_carrier_paragraph(self):
        out = categories.render_E1(_CFTR_F508DEL, disease_name="Cystic fibrosis")
        self.assertIn("Autosomal Recessive", out)
        self.assertIn("Heterozygous", out)
        self.assertIn("carrier", out.lower())
        self.assertIn("25%", out)
        self.assertIn("counselor", out.lower())


class CategoryF1Tests(SimpleTestCase):
    def test_filters_carrier_status_yes_AR(self):
        rows = [_CFTR_F508DEL, _SFTPC_I73T]
        out = categories.render_F1(rows)
        self.assertIn("CFTR", out)            # carrier of CF
        self.assertNotIn("SFTPC", out)        # no carrier flag
        self.assertIn("counselor", out.lower())


class CategoryF2Tests(SimpleTestCase):
    def test_empty_when_no_secondary_findings(self):
        out = categories.render_F2([_CFTR_F508DEL, _SFTPC_I73T])
        self.assertIn("does not list any secondary", out.lower())

    def test_lists_acmg_sf_rows(self):
        sf_row = _row(**{
            "Ref.Gene": "BRCA2",
            "ClinVar_Disease": "Hereditary breast and ovarian cancer",
            "clinvar: Clinvar": "Pathogenic",
            "Secondary_Finding": "YES",
            "Actionable": "YES",
            "OMIM_ID": "600185",
        })
        out = categories.render_F2([sf_row, _CFTR_F508DEL])
        self.assertIn("BRCA2", out)
        self.assertIn("Actionable", out)
        self.assertNotIn("CFTR", out)


# ---------------------------------------------------------------------------
# Full Stage-3 / Stage-6 wiring smoke test — filter_variants + renderer
# ---------------------------------------------------------------------------

class FilterPlusRenderTests(SimpleTestCase):
    """End-to-end: feed filter_variants the example rows, render C1.

    Exercises that the bucket carried by FilterResult survives all the
    way to the patient-facing string."""

    def test_lung_query_surfaces_cftr_and_sftpc(self):
        parsed = {
            "report": {
                "raw_rows": [_CFTR_F508DEL, _SFTPC_I73T],
            },
        }
        # Body-system gene set in the synthetic case is a 2-gene set —
        # we want the filter to keep both rows under different buckets.
        fr = tools.filter_variants(
            parsed, gene_filter={"CFTR", "SFTPC"},
            apply_pathogenicity_filter=True, max_results=10,
        )
        self.assertEqual(len(fr.matched), 2)
        # CFTR is Strong (1), SFTPC is VUS_HIGH_SCORE (5) — CFTR first.
        self.assertEqual(fr.matched[0]["Ref.Gene"], "CFTR")
        self.assertEqual(fr.buckets[0], Bucket.STRONG_PATHOGENIC)
        self.assertEqual(fr.matched[1]["Ref.Gene"], "SFTPC")
        self.assertEqual(fr.buckets[1], Bucket.VUS_HIGH_SCORE)
        out = categories.render_C1(
            body_system_name="Respiratory system",
            hpo_root_id="HP:0002086",
            matched_with_buckets=list(zip(fr.matched, fr.buckets)),
            universe=fr.universe,
        )
        self.assertIn("CFTR", out)
        self.assertIn("SFTPC", out)
        self.assertIn("Strong evidence", out)
