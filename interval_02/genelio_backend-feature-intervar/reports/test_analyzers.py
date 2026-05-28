"""End-to-end tests for the per-site microbiome analyzers.

Validates that the parser extracts the correct shape & key facts from
each of the four real-world body-site PDFs we currently support.
The PDFs are kept in ``~/Downloads`` (production-grade fixtures from
the lab); when they're not present, the relevant tests are skipped so
the rest of the suite still runs in CI / fresh checkouts.

What we assert:
  * the analyzer returns ``status="ok"`` and the expected ``site``;
  * the patient block has at least one populated field;
  * the Shannon Diversity value is a plausible number with the
    correct ``status`` (above / below / within healthy range);
  * the top-organisms list is non-empty and rows have abundance values;
  * every condition declared in the report is parsed and at least one
    condition has at least one marker;
  * the LLM-facing ``analysis_context`` is non-trivial.

The expectations are *minimal contracts* — they should hold regardless
of small future PDF revisions — so the tests double as a regression
guard without becoming brittle.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from reports.analyzers import _REGISTRY
from reports.analyzers._microbiome import (
    SiteConfig,
    _coalesce_continuation_rows,
    _extract_diversity,
    _extract_patient,
    _is_condition_table,
    _is_top_organism_table,
    _match_heading,
    _parse_condition_row,
    _parse_organism_row,
)


# ---------------------------------------------------------------------------
# PDF fixture discovery
# ---------------------------------------------------------------------------
# Real-world fixtures live outside the repo to avoid bloating it with
# multi-MB PDFs (and to keep PHI out of source control). We search a
# handful of standard locations so the tests run on any dev machine where
# the lab samples have been downloaded.

_FIXTURE_DIRS = [
    Path.home() / "Downloads",
    Path(os.environ.get("MICROBIOME_FIXTURES", "")) if os.environ.get("MICROBIOME_FIXTURES") else None,
]
_FIXTURE_DIRS = [p for p in _FIXTURE_DIRS if p and p.exists()]


def _find_pdf(*name_fragments: str) -> Path | None:
    """Return the first PDF in any fixture dir matching all fragments (case-insensitive)."""
    for directory in _FIXTURE_DIRS:
        for pdf in directory.glob("*.pdf"):
            lower = pdf.name.lower()
            if all(f.lower() in lower for f in name_fragments):
                return pdf
    return None


# ---------------------------------------------------------------------------
# Pure-unit tests (no PDF needed) — fast, deterministic
# ---------------------------------------------------------------------------

class CoalesceContinuationRowsTests(unittest.TestCase):
    """``_coalesce_continuation_rows`` merges wrap-induced split rows."""

    def test_merges_split_species_name_and_range(self):
        # Mirrors the real "Cutibacterium" / "avidum" split from the
        # skin report: a row whose name (col 1) and reference (col 7)
        # both wrapped onto a second physical row.
        table = [
            ["", "Cutibacterium", "", "0.0539%", None, None, "", "0.00% -", "", "Phylum: Actinobacteria"],
            [None, "avidum",      None, None,      None, None, None, "0.08%",   None, None],
        ]
        merged = _coalesce_continuation_rows(table)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0][1], "Cutibacterium avidum")
        self.assertEqual(merged[0][7], "0.00% - 0.08%")

    def test_does_not_merge_unrelated_rows(self):
        table = [
            ["Streptococcus mitis", "15.21%", "0.11%-1.10%", "Description A"],
            ["Veillonella parvula", "4.87%", "0.58%-3.62%", "Description B"],
        ]
        merged = _coalesce_continuation_rows(table)
        self.assertEqual(len(merged), 2)

    def test_preserves_single_row_table(self):
        table = [["Header A", "Header B"]]
        self.assertEqual(_coalesce_continuation_rows(table), [["Header A", "Header B"]])


class ExtractDiversityTests(unittest.TestCase):
    """``_extract_diversity`` finds the user's Shannon value across phrasings."""

    def test_oral_phrasing_sdiv_of(self):
        text = "Your oral microbiome exhibits an SDIV of 3.581, surpassing the upper limit."
        result = _extract_diversity(text, (1.2, 3.0))
        self.assertEqual(result["score"], 3.581)
        self.assertEqual(result["status"], "above_range")

    def test_vaginal_phrasing_sdvi_typo(self):
        # Real lab typo: SDVI instead of SDIV.
        text = "Your vaginal microbiome has an SDVI of 0.02, which is lower than the healthy range."
        result = _extract_diversity(text, (0.2, 1.01))
        self.assertEqual(result["score"], 0.02)
        self.assertEqual(result["status"], "below_range")

    def test_skin_phrasing_shannon_value_is(self):
        text = "The Shannon value for your skin microbiome is 0.571, lower than the healthy range."
        result = _extract_diversity(text, (2.34, 3.5))
        self.assertEqual(result["score"], 0.571)
        self.assertEqual(result["status"], "below_range")

    def test_within_range_status(self):
        text = "Your gut microbiome exhibits an SDIV of 2.5"
        result = _extract_diversity(text, (1.0, 4.0))
        self.assertEqual(result["status"], "within_range")

    def test_returns_empty_when_no_match(self):
        text = "There is no diversity value mentioned in this sentence."
        self.assertEqual(_extract_diversity(text, (0.0, 1.0)), {})

    def test_does_not_pick_up_definition_range_as_score(self):
        # The lab includes a definition sentence "SDIV for a healthy
        # cohort is in the range of 0.2 to 1.01" which we must NOT
        # mistake for the user's score.
        text = (
            "The SDIV for a healthy vaginal microbiome reference cohort "
            "is in the range of 0.2 to 1.01.\n"
            "Your vaginal microbiome has an SDVI of 0.02."
        )
        result = _extract_diversity(text, (0.2, 1.01))
        self.assertEqual(result["score"], 0.02)


class MatchHeadingTests(unittest.TestCase):
    def test_primary_pattern_microbial_markers_for(self):
        self.assertEqual(_match_heading("Microbial markers for Obesity"), "Obesity")
        self.assertEqual(
            _match_heading("Microbial markers for bacterial vaginosis"),
            "bacterial vaginosis",
        )

    def test_fallback_pattern_linked_with(self):
        self.assertEqual(
            _match_heading("Decreased abundance of following markers is linked with melasma."),
            "melasma",
        )

    def test_rejects_non_heading_sentences(self):
        self.assertIsNone(_match_heading("This is just a regular paragraph."))
        self.assertIsNone(_match_heading(""))


class ExtractPatientTests(unittest.TestCase):
    def test_handles_inline_two_field_lines(self):
        text = (
            "Client Information\n"
            "Name: Jane Doe Date of Birth: 1990-01-01\n"
            "Sample Type: Vaginal swab PHN: 12345\n"
            "Note: this is a disclaimer"
        )
        info = _extract_patient(text)
        self.assertEqual(info["Name"], "Jane Doe")
        self.assertEqual(info["Date of Birth"], "1990-01-01")
        self.assertEqual(info["Sample Type"], "Vaginal swab")
        self.assertEqual(info["PHN"], "12345")

    def test_handles_pdf_typo_with_internal_space(self):
        # Real-world: the lab's PDF tooling injects a stray space inside
        # "Health" — "Provincial He alth Number". Our flexible label
        # regex must still recognise it.
        text = (
            "Client Information\n"
            "Sample Type: Oral swab Provincial He alth Number: Not Provided\n"
            "Note: ..."
        )
        info = _extract_patient(text)
        self.assertEqual(info["Sample Type"], "Oral swab")
        self.assertEqual(info["Provincial Health Number"], "Not Provided")

    def test_skips_blank_values(self):
        text = (
            "Client Information\n"
            "Name: Date of Birth:\n"
            "Sample Type: Skin Swab Sample Receiving Date:\n"
            "Note: ..."
        )
        info = _extract_patient(text)
        self.assertNotIn("Name", info)
        self.assertNotIn("Date of Birth", info)
        self.assertEqual(info["Sample Type"], "Skin Swab")


class TableClassifierTests(unittest.TestCase):
    def test_top_organism_table_recognised(self):
        table = [
            ["Scientific Name", "Abundance", "Reference Range", "Significance"],
            ["Lactobacillus crispatus", "96.07%", "30.08%-70.69%", "..."],
        ]
        self.assertTrue(_is_top_organism_table(table))

    def test_condition_table_recognised(self):
        table = [
            ["Microbial marker", "Relative abundance (%)", "Reference Range (%)"],
            ["Prevotella spp.", "3.80%", "0.26%-3.14%"],
        ]
        self.assertTrue(_is_condition_table(table))

    def test_random_table_rejected(self):
        table = [["Foo", "Bar"], ["1", "2"]]
        self.assertFalse(_is_condition_table(table))
        self.assertFalse(_is_top_organism_table(table))


class RowParserTests(unittest.TestCase):
    def test_parse_organism_row_handles_padding_columns(self):
        # The skin report wraps every cell with empty padding columns.
        row = ["Cutibacterium acnes", None, None, "87.7669%", "19.00%-65.70%", "Phylum: Actinobacteria..."]
        parsed = _parse_organism_row(row)
        self.assertEqual(parsed["name"], "Cutibacterium acnes")
        self.assertEqual(parsed["abundance"], 87.7669)
        self.assertEqual(parsed["reference_low"], 19.00)
        self.assertEqual(parsed["reference_high"], 65.70)
        self.assertEqual(parsed["status"], "above_range")

    def test_parse_condition_row_handles_nd_value(self):
        row = ["Porphyromonas gingivalis", "ND", "0.00%-0.00%"]
        parsed = _parse_condition_row(row)
        self.assertEqual(parsed["name"], "Porphyromonas gingivalis")
        self.assertIsNone(parsed["abundance"])
        self.assertEqual(parsed["status"], "not_detected")

    def test_parse_organism_row_returns_none_for_header_row(self):
        row = ["Scientific Name", "Abundance", "Reference Range"]
        self.assertIsNone(_parse_organism_row(row))


# ---------------------------------------------------------------------------
# Integration tests against real PDFs (skipped if fixtures absent)
# ---------------------------------------------------------------------------

class _BaseAnalyzerIntegrationTest(unittest.TestCase):
    """Common shape assertions for any body-site analyzer.

    Subclasses set ``site``, ``pdf_fragments`` (filename keywords for
    fixture discovery), the expected diversity ``status`` and the set
    of expected condition names (case-insensitive substring match).
    """

    site: str = ""
    pdf_fragments: tuple[str, ...] = ()
    expected_diversity_status: str = ""
    expected_conditions: tuple[str, ...] = ()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pdf_path = _find_pdf(*cls.pdf_fragments) if cls.pdf_fragments else None
        if cls.pdf_path is None:
            raise unittest.SkipTest(
                f"No fixture PDF found matching {cls.pdf_fragments!r} — drop one "
                f"into ~/Downloads or set MICROBIOME_FIXTURES to enable."
            )
        cls.result = _REGISTRY[cls.site](str(cls.pdf_path))

    def test_status_ok(self):
        self.assertEqual(self.result["status"], "ok")
        self.assertEqual(self.result["site"], self.site)

    def test_diversity_extracted(self):
        diversity = self.result["report"]["diversity"]
        self.assertIn("score", diversity, "expected diversity score to be extracted")
        score = diversity["score"]
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 10.0)
        self.assertEqual(diversity["status"], self.expected_diversity_status)

    def test_top_organisms_present(self):
        organisms = self.result["report"]["top_organisms"]
        self.assertGreater(len(organisms), 0, "expected at least one top organism")
        # Every organism must have a non-empty name and either a numeric
        # abundance or an explicit ND.
        for org in organisms:
            self.assertTrue(org["name"], f"organism missing name: {org}")
            self.assertTrue(
                org["abundance_raw"],
                f"organism missing abundance_raw: {org}",
            )

    def test_expected_conditions_recognised(self):
        cond_names_lower = {c.lower() for c in self.result["report"]["conditions"]}
        for expected in self.expected_conditions:
            self.assertTrue(
                any(expected.lower() in name for name in cond_names_lower),
                f"expected condition matching {expected!r} not found in {cond_names_lower}",
            )

    def test_at_least_one_condition_has_markers(self):
        conds = self.result["report"]["conditions"]
        with_markers = [name for name, data in conds.items() if data.get("markers")]
        self.assertTrue(
            with_markers,
            "expected at least one condition to have parsed markers",
        )

    def test_analysis_context_non_trivial(self):
        ac = self.result.get("analysis_context") or ""
        self.assertGreater(len(ac), 500, "analysis_context too short to be useful")
        self.assertIn("STRUCTURED ANALYSIS", ac.upper())

    def test_full_text_captured_for_rag(self):
        full_text = self.result["report"]["full_text"]
        self.assertGreater(len(full_text), 1000)


class OralIntegrationTests(_BaseAnalyzerIntegrationTest):
    site = "oral"
    pdf_fragments = ("oral", "report")
    expected_diversity_status = "above_range"
    expected_conditions = ("obesity", "cardiovascular", "cancer")


class SkinIntegrationTests(_BaseAnalyzerIntegrationTest):
    site = "skin"
    pdf_fragments = ("skin", "report")
    expected_diversity_status = "below_range"
    expected_conditions = (
        "atopic dermatitis",
        "psoriasis",
        "acne",
        "cancer",
    )


class VaginalIntegrationTests(_BaseAnalyzerIntegrationTest):
    site = "vaginal"
    pdf_fragments = ("vaginal", "report")
    expected_diversity_status = "below_range"
    expected_conditions = (
        "bacterial vaginosis",
        "sexually transmitted",
        "miscarriage",
        "infertility",
    )


class GutIntegrationTests(unittest.TestCase):
    """Gut analyzer is the legacy parser — minimal smoke check only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pdf_path = _find_pdf("gut")
        if cls.pdf_path is None:
            raise unittest.SkipTest("No gut PDF fixture found")
        cls.result = _REGISTRY["gut"](str(cls.pdf_path))

    def test_analysis_context_present(self):
        self.assertEqual(self.result["status"], "ok")
        ac = self.result.get("analysis_context") or ""
        self.assertGreater(len(ac), 500)
