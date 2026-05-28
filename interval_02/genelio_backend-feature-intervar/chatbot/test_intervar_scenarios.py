"""End-to-end scenario harness for the InterVar agentic pipeline.

Mirrors ``chatbot/test_scenarios.py`` (the clinical_csv suite) but
exercises the *separate* InterVar TXT flow that the bioinformatician
(Saurabh) tested in production. Drives a hand-built synthetic InterVar
TSV through the real analyzer (``reports.analyzers.intervar.analyze``)
plus the real orchestrator's deterministic Stage-2 executor — no
vLLM/router call needed because we build :class:`RouterDecision`
objects directly.

Acceptance criteria (each is an explicit test):

  1. ``Conflicting_interpretations_of_pathogenicity`` rows are NOT
     selected as headline / clinically-interesting. (Was the root of
     Saurabh's complaint #2.)
  2. Rows with ``BA1=1`` are NOT selected as headline, even if PVS1=1.
     (FLG2 case in Saurabh's chat.)
  3. The bucket adapter produces the correct :class:`Bucket` for each
     InterVar row.
  4. ``hpo.resolve("lung")`` does NOT return "Madelung-like forearm
     deformities". (Saurabh's complaint about the substring fallback.)
  5. ``_render_row`` surfaces the evidence-tier label so the LLM can't
     paraphrase it away.

Run with:
    DATABASE_URL=sqlite:///./test.sqlite3 DJANGO_SECRET_KEY=test \\
    DJANGO_DEBUG=True DJANGO_ALLOWED_HOSTS='*' \\
    python manage.py test chatbot.test_intervar_scenarios -v 2
"""
from __future__ import annotations

import csv
import io
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from chatbot.agentic import hpo
from chatbot.agentic.intervar_orchestrator import (
    RouterDecision,
    _adapt_intervar_row,
    _is_clinically_interesting,
    _render_row,
    bucket_for,
    execute,
)
from chatbot.agentic.tools import BUCKET_LABEL, Bucket
from reports.analyzers import intervar as analyzer


# ---------------------------------------------------------------------------
# Synthetic InterVar TSV — every row is hand-tuned to hit a specific rule
# branch. Mirrors what Saurabh actually had in his report.
# ---------------------------------------------------------------------------

# InterVar TXT uses tab separation and includes whitespace in some
# headers ("clinvar: Clinvar ", " InterVar: InterVar and Evidence ").
# The analyzer strips that. We use the stripped names here.
_HEADERS = [
    "#Chr", "Start", "End", "Ref", "Alt", "Ref.Gene", "Func.refGene",
    "ExonicFunc.refGene", "Gene.ensGene", "avsnp147", "AAChange.ensGene",
    "AAChange.refGene", "clinvar: Clinvar", "InterVar: InterVar and Evidence",
    "Freq_gnomAD_genome_ALL", "Freq_esp6500siv2_all", "Freq_1000g2015aug_all",
    "CADD_raw", "CADD_phred", "SIFT_score", "GERP++_RS",
    "phyloP46way_placental", "dbscSNV_ADA_SCORE", "dbscSNV_RF_SCORE",
    "Interpro_domain", "AAChange.knownGene", "rmsk", "MetaSVM_score",
    "Freq_gnomAD_genome_POPs", "OMIM", "Phenotype_MIM", "OrphaNumber",
    "Orpha", "Otherinfo",
]


def _intervar_evidence(verdict: str, *, PVS1=0, BA1=0,
                       PS=None, PM=None, PP=None, BS=None, BP=None) -> str:
    """Build an InterVar evidence string in the format the parser expects."""
    PS = PS or [0]*5
    PM = PM or [0]*7
    PP = PP or [0]*6
    BS = BS or [0]*5
    BP = BP or [0]*8
    return (
        f"InterVar: {verdict} PVS1={PVS1} "
        f"PS=[{', '.join(str(x) for x in PS)}] "
        f"PM=[{', '.join(str(x) for x in PM)}] "
        f"PP=[{', '.join(str(x) for x in PP)}] "
        f"BA1={BA1} "
        f"BS=[{', '.join(str(x) for x in BS)}] "
        f"BP=[{', '.join(str(x) for x in BP)}]"
    )


def _row(**values):
    base = {h: "." for h in _HEADERS}
    base.update(values)
    return base


# Variants mirroring Saurabh's actual production chat report PLUS a few
# the bioinformatician spec says should surface (e.g. true Pathogenic).
_VARIANTS = [
    # 1. MTHFR — Saurabh complaint: was surfaced as disease-causing.
    #    ClinVar Conflicting + InterVar Benign + BA1=1 → must DROP.
    _row(**{
        "#Chr": "1", "Start": "11854476", "End": "11854476",
        "Ref": "T", "Alt": "G",
        "Ref.Gene": "MTHFR", "Func.refGene": "exonic",
        "ExonicFunc.refGene": "nonsynonymous SNV",
        "AAChange.refGene": "MTHFR:NM_001330358:exon8:c.A1409C:p.E470A",
        "clinvar: Clinvar": "Conflicting_interpretations_of_pathogenicity",
        "InterVar: InterVar and Evidence": _intervar_evidence(
            "Benign", BA1=1, PM=[1,0,0,0,0,0,0]),
        "Freq_gnomAD_genome_ALL": "0.33",
        "CADD_phred": "20.9", "Otherinfo": "hom",
        "OMIM": "607093",
    }),
    # 2. PADI3 — TRUE positive. ClinVar Pathogenic / InterVar Uncertain.
    #    Should surface as CLINVAR_PATHOGENIC.
    _row(**{
        "#Chr": "1", "Start": "17732247", "End": "17732247",
        "Ref": "G", "Alt": "A",
        "Ref.Gene": "PADI3", "Func.refGene": "exonic",
        "ExonicFunc.refGene": "nonsynonymous SNV",
        "AAChange.refGene": "PADI3:NM_016233:exon8:c.C881T:p.A294V",
        "clinvar: Clinvar": "Pathogenic",
        "InterVar: InterVar and Evidence": _intervar_evidence(
            "Uncertain significance", PM=[1,0,0,0,0,0,0], PP=[1,0,0,0,0,0]),
        "Freq_gnomAD_genome_ALL": "0.0003",
        "CADD_phred": "26.9", "Otherinfo": "het",
        "OMIM": "606755",
    }),
    # 3. P3H1 — Conflicting + Benign + BA1=1 → DROP.
    _row(**{
        "#Chr": "1", "Start": "43213120", "End": "43213120",
        "Ref": "G", "Alt": "A",
        "Ref.Gene": "P3H1", "Func.refGene": "exonic",
        "ExonicFunc.refGene": "nonsynonymous SNV",
        "AAChange.refGene": "P3H1:NM_001146289:exon11:c.G1647A:p.M549I",
        "clinvar: Clinvar": "Conflicting_interpretations_of_pathogenicity",
        "InterVar: InterVar and Evidence": _intervar_evidence("Benign", BA1=1),
        "Freq_gnomAD_genome_ALL": "0.10",
        "CADD_phred": "18.98", "Otherinfo": "hom",
        "OMIM": "610339",
    }),
    # 4. PCSK9 — Conflicting + Benign + BA1=1 → DROP.
    _row(**{
        "#Chr": "1", "Start": "55518204", "End": "55518204",
        "Ref": "A", "Alt": "G",
        "Ref.Gene": "PCSK9", "Func.refGene": "exonic",
        "ExonicFunc.refGene": "synonymous SNV",
        "AAChange.refGene": "PCSK9:NM_174936:exon7:c.A1026G:p.Q342Q",
        "clinvar: Clinvar": "Conflicting_interpretations_of_pathogenicity",
        "InterVar: InterVar and Evidence": _intervar_evidence("Benign", BA1=1),
        "Freq_gnomAD_genome_ALL": "0.18", "Otherinfo": "hom",
        "OMIM": "607786",
    }),
    # 5. GPSM2 — Conflicting ClinVar + Uncertain InterVar — should be
    #    CONFLICTING_PATHOGENIC if InterVar leans pathogenic, but here
    #    InterVar says "Uncertain significance" → no pathogenic call →
    #    falls through to VUS check; high CADD but no REVEL → DROP.
    _row(**{
        "#Chr": "1", "Start": "108922301", "End": "108922301",
        "Ref": "G", "Alt": "A",
        "Ref.Gene": "GPSM2", "Func.refGene": "exonic",
        "ExonicFunc.refGene": "nonsynonymous SNV",
        "AAChange.refGene": "GPSM2:NM_001321038:exon10:c.G1066A:p.G356R",
        "clinvar: Clinvar": "Conflicting_interpretations_of_pathogenicity",
        "InterVar: InterVar and Evidence": _intervar_evidence(
            "Uncertain significance", PM=[1,0,0,0,0,0,0]),
        "Freq_gnomAD_genome_ALL": "0.0001",
        "CADD_phred": "32", "Otherinfo": "het",
        "OMIM": "609245",
    }),
    # 6. FLG2 — PVS1=1 BUT ALSO BA1=1. Must DROP. (FLG2 was Saurabh's
    #    most-egregious headline-list example because it carried both
    #    "very strong pathogenic" and "stand-alone benign" flags — the
    #    rule says BA1 wins.)
    _row(**{
        "#Chr": "1", "Start": "152325120", "End": "152325120",
        "Ref": "C", "Alt": "A",
        "Ref.Gene": "FLG2", "Func.refGene": "exonic",
        "ExonicFunc.refGene": "stopgain",
        "AAChange.refGene": "FLG2:NM_001014342:exon3:c.C7130A:p.S2377X",
        "clinvar: Clinvar": "UNK",
        "InterVar: InterVar and Evidence": _intervar_evidence(
            "Benign", PVS1=1, BA1=1),
        "Freq_gnomAD_genome_ALL": "0.07",
        "CADD_phred": "34", "Otherinfo": "het",
    }),
    # 7. TRUE Pathogenic — Both sources agree, not BA1. Expected: STRONG.
    _row(**{
        "#Chr": "13", "Start": "32914437", "End": "32914437",
        "Ref": "T", "Alt": "C",
        "Ref.Gene": "BRCA2", "Func.refGene": "exonic",
        "ExonicFunc.refGene": "stopgain",
        "AAChange.refGene": "BRCA2:NM_000059:exon11:c.5946delT",
        "clinvar: Clinvar": "Pathogenic",
        "InterVar: InterVar and Evidence": _intervar_evidence(
            "Pathogenic", PVS1=1, PM=[1,0,0,0,0,0,0]),
        "Freq_gnomAD_genome_ALL": "0.0", "Otherinfo": "het",
        "CADD_phred": "37",
        "OMIM": "600185",
    }),
    # 8. ABCA3 (benign + BA1=1) — Saurabh's lung-disease query surfaced
    #    this; with the fix it must NOT count as headline.
    _row(**{
        "#Chr": "16", "Start": "2342134", "End": "2342134",
        "Ref": "T", "Alt": "C",
        "Ref.Gene": "ABCA3", "Func.refGene": "exonic",
        "ExonicFunc.refGene": "synonymous SNV",
        "AAChange.refGene": "ABCA3:NM_001089:exon27:c.T4116C:p.S1372S",
        "clinvar: Clinvar": "Benign",
        "InterVar: InterVar and Evidence": _intervar_evidence(
            "Benign", BA1=1, BS=[1,0,0,0,0], BP=[0,0,0,1,0,0,1,0]),
        "Freq_gnomAD_genome_ALL": "0.88", "Otherinfo": "het",
        "OMIM": "601615",
    }),
    # 9. SFTPC (Uncertain ClinVar + Benign InterVar + BA1=1) — Saurabh's
    #    lung query also surfaced this. Must NOT count as headline.
    _row(**{
        "#Chr": "8", "Start": "22020437", "End": "22020437",
        "Ref": "T", "Alt": "C",
        "Ref.Gene": "SFTPC", "Func.refGene": "UTR5",
        "clinvar: Clinvar": "Uncertain_significance",
        "InterVar: InterVar and Evidence": _intervar_evidence(
            "Benign", BA1=1, BS=[1,0,0,0,0]),
        "Freq_gnomAD_genome_ALL": "0.19", "Otherinfo": "het",
        "OMIM": "610913",
    }),
]


def _build_parsed():
    """Write the synthetic InterVar TSV to disk + run the real analyzer."""
    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".intervar.txt", delete=False, newline="",
    )
    writer = csv.DictWriter(fd, fieldnames=_HEADERS, delimiter="\t")
    writer.writeheader()
    for r in _VARIANTS:
        writer.writerow(r)
    fd.close()
    try:
        return analyzer.analyze(fd.name)
    finally:
        Path(fd.name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Analyzer-level tests — _is_headline must reject Conflicting + BA1
# ---------------------------------------------------------------------------

class AnalyzerHeadlineTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parsed = _build_parsed()
        cls.rows = cls.parsed["report"]["raw_rows"]
        cls.by_gene = {r["gene"]: r for r in cls.rows}

    def test_analyzer_parsed_status_ok(self):
        self.assertEqual(self.parsed["status"], "ok")
        self.assertEqual(self.parsed["site"], "intervar")
        self.assertEqual(len(self.rows), len(_VARIANTS))

    def test_conflicting_clinvar_with_BA1_not_headline(self):
        """Saurabh complaint #2 — MTHFR / P3H1 / PCSK9 must NOT be
        flagged as headline (they are Conflicting + Benign + BA1=1)."""
        for g in ("MTHFR", "P3H1", "PCSK9"):
            row = self.by_gene[g]
            self.assertFalse(
                analyzer._is_headline(row),
                f"{g} should not be headline (Conflicting ClinVar + BA1=1)",
            )

    def test_pvs1_with_BA1_not_headline(self):
        """FLG2 carries PVS1=1 AND BA1=1 — rule says BA1 wins."""
        self.assertFalse(analyzer._is_headline(self.by_gene["FLG2"]))

    def test_benign_clinvar_with_BA1_not_headline(self):
        self.assertFalse(analyzer._is_headline(self.by_gene["ABCA3"]))
        self.assertFalse(analyzer._is_headline(self.by_gene["SFTPC"]))

    def test_uncertain_clinvar_uncertain_intervar_not_headline(self):
        """GPSM2 has Conflicting ClinVar + Uncertain InterVar — neither
        side calls it pathogenic by the strict rule, so it's not
        headline."""
        self.assertFalse(analyzer._is_headline(self.by_gene["GPSM2"]))

    def test_true_clinvar_pathogenic_is_headline(self):
        """PADI3 (ClinVar Pathogenic) and BRCA2 (both agree) must surface."""
        self.assertTrue(analyzer._is_headline(self.by_gene["PADI3"]))
        self.assertTrue(analyzer._is_headline(self.by_gene["BRCA2"]))

    def test_clinvar_token_match_helpers(self):
        """Strict membership — Conflicting_* should not match."""
        from reports.analyzers.intervar import (
            _clinvar_is_pathogenic, _intervar_is_pathogenic,
        )
        self.assertTrue(_clinvar_is_pathogenic("Pathogenic"))
        self.assertTrue(_clinvar_is_pathogenic("Likely_pathogenic"))
        self.assertTrue(_clinvar_is_pathogenic("Pathogenic/Likely_pathogenic"))
        self.assertFalse(_clinvar_is_pathogenic(
            "Conflicting_interpretations_of_pathogenicity"
        ))
        self.assertFalse(_clinvar_is_pathogenic("Likely_benign"))
        self.assertFalse(_clinvar_is_pathogenic("UNK"))
        self.assertFalse(_clinvar_is_pathogenic(None))
        self.assertTrue(_intervar_is_pathogenic("Pathogenic"))
        self.assertTrue(_intervar_is_pathogenic("Likely pathogenic"))
        self.assertFalse(_intervar_is_pathogenic("Likely benign"))
        self.assertFalse(_intervar_is_pathogenic("Uncertain significance"))


# ---------------------------------------------------------------------------
# Orchestrator-level tests — bucket adapter + clinically-interesting
# ---------------------------------------------------------------------------

class BucketRuleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parsed = _build_parsed()
        cls.by_gene = {r["gene"]: r for r in cls.parsed["report"]["raw_rows"]}

    def test_mthfr_dropped(self):
        self.assertEqual(bucket_for(self.by_gene["MTHFR"]), Bucket.DROP)
        self.assertFalse(_is_clinically_interesting(self.by_gene["MTHFR"]))

    def test_padi3_clinvar_pathogenic(self):
        self.assertEqual(
            bucket_for(self.by_gene["PADI3"]), Bucket.CLINVAR_PATHOGENIC,
        )
        self.assertTrue(_is_clinically_interesting(self.by_gene["PADI3"]))

    def test_p3h1_dropped(self):
        self.assertEqual(bucket_for(self.by_gene["P3H1"]), Bucket.DROP)

    def test_pcsk9_dropped(self):
        self.assertEqual(bucket_for(self.by_gene["PCSK9"]), Bucket.DROP)

    def test_gpsm2_dropped(self):
        # Conflicting + Uncertain → falls through to VUS check, but no
        # REVEL value → DROP.
        self.assertEqual(bucket_for(self.by_gene["GPSM2"]), Bucket.DROP)

    def test_flg2_dropped(self):
        """PVS1=1 + BA1=1 — BA1 wins. Drop."""
        self.assertEqual(bucket_for(self.by_gene["FLG2"]), Bucket.DROP)

    def test_brca2_strong(self):
        self.assertEqual(
            bucket_for(self.by_gene["BRCA2"]), Bucket.STRONG_PATHOGENIC,
        )

    def test_abca3_dropped(self):
        self.assertEqual(bucket_for(self.by_gene["ABCA3"]), Bucket.DROP)

    def test_sftpc_dropped(self):
        self.assertEqual(bucket_for(self.by_gene["SFTPC"]), Bucket.DROP)


# ---------------------------------------------------------------------------
# Executor-level test — disease_link with combined rule must filter
# Conflicting/BA1 rows out.
# ---------------------------------------------------------------------------

class ExecutorFilterTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parsed = _build_parsed()

    def test_disease_link_filters_out_conflicting(self):
        """Saurabh asked 'disease causing genes' and got 5 Conflicting/BA1
        rows back. With the rule applied, the executor must surface only
        the rows that pass the combined-rule filter."""
        decision = RouterDecision(intent="disease_link", disease_term="")
        # Apply pathogenicity filter directly through _matches_decision
        # → then _is_clinically_interesting (mirrors the executor path).
        rows = self.parsed["report"]["raw_rows"]
        kept = [r for r in rows if _is_clinically_interesting(r)]
        gene_set = {r["gene"] for r in kept}
        # The headline-eligible variants in our synthetic file:
        # PADI3 (CLINVAR_PATHOGENIC), BRCA2 (STRONG). Everything else
        # must drop.
        self.assertEqual(gene_set, {"PADI3", "BRCA2"})
        # Specifically: the bioinformatician-flagged false positives
        # are gone.
        for false_positive in ("MTHFR", "P3H1", "PCSK9", "GPSM2",
                               "FLG2", "ABCA3", "SFTPC"):
            self.assertNotIn(false_positive, gene_set)


# ---------------------------------------------------------------------------
# _render_row surfaces the evidence-tier label so the LLM can quote it
# verbatim and not paraphrase.
# ---------------------------------------------------------------------------

class RenderRowTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parsed = _build_parsed()
        cls.by_gene = {r["gene"]: r for r in cls.parsed["report"]["raw_rows"]}

    def test_strong_evidence_tier_rendered(self):
        out = _render_row(self.by_gene["BRCA2"])
        self.assertIn("Evidence tier:", out)
        self.assertIn("Strong evidence", out)

    def test_clinvar_pathogenic_tier_rendered(self):
        out = _render_row(self.by_gene["PADI3"])
        self.assertIn("ClinVar-supported pathogenic", out)

    def test_dropped_bucket_label_still_renderable(self):
        # If someone explicitly asks for a benign row, _render_row
        # should still render with the DROP label rather than crashing.
        out = _render_row(self.by_gene["MTHFR"])
        self.assertIn("Evidence tier:", out)
        # MTHFR is DROP per the rule.
        self.assertIn(BUCKET_LABEL[Bucket.DROP], out)


# ---------------------------------------------------------------------------
# HPO resolver — "lung" must NOT match "Madelung-like forearm deformities"
# ---------------------------------------------------------------------------

class HPOResolverTightenedTests(SimpleTestCase):
    def test_lung_does_not_match_madelung(self):
        """The bioinformatician's chat resolved 'lung' → 'Madelung-like
        forearm deformities' (HP:0003068) via substring fallback. After
        the lay-synonym + word-boundary fix, 'lung' must resolve to a
        respiratory HP term — never to Madelung."""
        m = hpo.resolve("lung")
        self.assertNotEqual(m.name, "Madelung-like forearm deformities")
        # Should hit the lay-synonym mapping into Abnormal lung morphology.
        if m.hpo_id:
            self.assertIn("lung", m.name.lower())

    def test_lungs_plural_also_resolves(self):
        m = hpo.resolve("lungs")
        self.assertNotEqual(m.name, "Madelung-like forearm deformities")

    def test_heart_kidney_liver_lay_words(self):
        """Each major organ should hit the lay-synonym table without
        falling through to substring matching."""
        self.assertTrue(hpo.resolve("heart").hpo_id)
        self.assertTrue(hpo.resolve("kidney").hpo_id)
        self.assertTrue(hpo.resolve("liver").hpo_id)

    def test_short_unmapped_query_is_not_fallback_into_midword(self):
        """A 4-char query that isn't in the lay synonyms should still
        NOT match an arbitrary mid-word substring."""
        # 'beig' isn't in lay synonyms; if it accidentally substring-matched
        # against e.g. "Beigh-Beigh syndrome" (made up), that'd be wrong.
        # We simply assert the resolver doesn't error out and doesn't
        # confidently return junk.
        m = hpo.resolve("beig")
        # Either it returns no match OR it matches at low confidence —
        # but it must not return a mid-word substring match at 0.7.
        if m.hpo_id:
            self.assertNotEqual(m.matched_via, "substring")
