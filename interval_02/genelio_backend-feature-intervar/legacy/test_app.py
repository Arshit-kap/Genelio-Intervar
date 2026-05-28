"""
Unit tests for Genelio Bot — covers PDF extraction, chunking, retrieval,
structured report parsing, LLM integration, and Gradio event handlers.
"""

import os
import re
import unittest

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

import pdfplumber
import chromadb
from sentence_transformers import SentenceTransformer

SAMPLE_PDF = "/Users/user/Downloads/Sample_output/Gut_microbiome_sample_report.pdf"
VLLM_BASE_URL = "http://localhost:8011/v1"
MODEL_NAME = "qwen3-30b"

embedder = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
chroma_client = chromadb.Client()


# ===========================================================================
# Test: PDF Extraction
# ===========================================================================

class TestPDFExtraction(unittest.TestCase):

    def test_extract_returns_pages(self):
        from app import extract_text_from_pdf
        pages = extract_text_from_pdf(SAMPLE_PDF)
        self.assertIsInstance(pages, list)
        self.assertGreater(len(pages), 0)

    def test_extract_page_structure(self):
        from app import extract_text_from_pdf
        pages = extract_text_from_pdf(SAMPLE_PDF)
        for p in pages:
            self.assertIn("page", p)
            self.assertIn("text", p)
            self.assertGreater(len(p["text"]), 0)

    def test_extract_skips_blank_pages(self):
        from app import extract_text_from_pdf
        pages = extract_text_from_pdf(SAMPLE_PDF)
        page_numbers = [p["page"] for p in pages]
        self.assertNotIn(1, page_numbers)

    def test_extract_page_count(self):
        from app import extract_text_from_pdf
        pages = extract_text_from_pdf(SAMPLE_PDF)
        self.assertEqual(len(pages), 21)

    def test_extract_nonexistent_file(self):
        from app import extract_text_from_pdf
        with self.assertRaises(Exception):
            extract_text_from_pdf("/nonexistent/path.pdf")


# ===========================================================================
# Test: Chunking
# ===========================================================================

class TestChunking(unittest.TestCase):

    def test_chunk_ids_unique(self):
        from app import extract_text_from_pdf, chunk_pages
        pages = extract_text_from_pdf(SAMPLE_PDF)
        chunks = chunk_pages(pages)
        ids = [c["id"] for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_chunk_overlap(self):
        from app import chunk_pages, CHUNK_SIZE, CHUNK_OVERLAP
        pages = [{"page": 1, "text": "A" * (CHUNK_SIZE + 500)}]
        chunks = chunk_pages(pages)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0]["text"][-CHUNK_OVERLAP:], chunks[1]["text"][:CHUNK_OVERLAP])

    def test_chunk_empty_pages(self):
        from app import chunk_pages
        self.assertEqual(chunk_pages([]), [])

    def test_tagged_text(self):
        from app import chunk_pages
        pages = [{"page": 3, "text": "Diversity Index\nDetails."}]
        chunks = chunk_pages(pages)
        self.assertIn("[Section: Diversity Index]", chunks[0]["tagged_text"])
        self.assertIn("[Page 3]", chunks[0]["tagged_text"])


# ===========================================================================
# Test: Vector Store & Retrieval
# ===========================================================================

class TestVectorStore(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from app import extract_text_from_pdf, chunk_pages, build_vector_store
        pages = extract_text_from_pdf(SAMPLE_PDF)
        chunks = chunk_pages(pages)
        cls.collection = build_vector_store(chunks)
        cls.chunk_count = len(chunks)

    def test_build_store(self):
        self.assertEqual(self.collection.count(), self.chunk_count)

    def test_retrieve_returns_string(self):
        from app import retrieve
        result = retrieve(self.collection, "gut diversity")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_retrieve_relevance_diversity(self):
        from app import retrieve
        result = retrieve(self.collection, "What is my Shannon Diversity?")
        self.assertTrue("diversity" in result.lower() or "shannon" in result.lower())

    def test_retrieve_relevance_keystone(self):
        from app import retrieve
        result = retrieve(self.collection, "Which keystone species am I missing?")
        self.assertIn("keystone", result.lower())

    def test_retrieve_relevance_pathogens(self):
        from app import retrieve
        result = retrieve(self.collection, "Do I have any pathogens?")
        self.assertTrue("pathogen" in result.lower() or "salmonella" in result.lower())


# ===========================================================================
# Test: Structured Report Parser
# ===========================================================================

class TestReportParser(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from report_analyzer import extract_all_tables
        cls.report = extract_all_tables(SAMPLE_PDF)

    def test_patient_info(self):
        self.assertEqual(self.report["patient"]["Name"], "Regina Levy")

    def test_diversity(self):
        d = self.report["diversity"]
        self.assertAlmostEqual(d["score"], 3.288)
        self.assertEqual(d["status"], "within_range")

    def test_fb_ratio(self):
        fb = self.report["fb_ratio"]
        self.assertAlmostEqual(fb["ratio"], 2.73)
        self.assertEqual(fb["status"], "above_range")

    def test_keystone_present(self):
        present = self.report["keystone_present"]
        self.assertGreater(len(present), 0)
        names = [o["name"] for o in present]
        self.assertIn("Bacteroides fragilis", names)
        self.assertIn("Akkermansia muciniphila", names)

    def test_keystone_missing(self):
        missing = self.report["keystone_missing"]
        self.assertEqual(len(missing), 4)
        self.assertIn("Ruminococcus bromii", missing)
        self.assertIn("Clostridium butyricum", missing)
        self.assertIn("Methanobrevibacter smithii", missing)
        self.assertIn("Bifidobacterium pseudolongum", missing)

    def test_keystone_dietary(self):
        dietary = self.report["keystone_dietary"]
        self.assertEqual(len(dietary), 4)
        species = [d["species"] for d in dietary]
        self.assertIn("Ruminococcus bromii", species)

    def test_top_organisms(self):
        top = self.report["top_organisms"]
        self.assertGreater(len(top), 5)
        names = [o["name"] for o in top]
        self.assertIn("Phocaeicola dorei", names)

    def test_conditions_exist(self):
        conditions = self.report["conditions"]
        self.assertGreater(len(conditions), 5)
        cond_keys = list(conditions.keys())
        self.assertTrue(any("depression" in k for k in cond_keys))
        self.assertTrue(any("obesity" in k for k in cond_keys))
        self.assertTrue(any("ibd" in k or "inflammatory" in k for k in cond_keys))

    def test_depression_out_of_range(self):
        """The user's key feedback — depression markers above range must be flagged."""
        conditions = self.report["conditions"]
        depression = None
        for k, v in conditions.items():
            if "depression" in k:
                depression = v
                break
        self.assertIsNotNone(depression, "Depression condition not found")

        out_of_range = [
            m for m in depression["markers"]
            if m["status"] in ("above_range", "below_range")
        ]
        self.assertGreater(len(out_of_range), 0, "Should flag out-of-range depression markers")

        # Specifically: Eggerthella spp. should be above range
        names = [m["name"] for m in out_of_range]
        self.assertTrue(
            any("eggerthella" in n.lower() for n in names),
            f"Eggerthella should be flagged, got: {names}",
        )

    def test_obesity_markers(self):
        conditions = self.report["conditions"]
        obesity = None
        for k, v in conditions.items():
            if "obesity" in k:
                obesity = v
                break
        self.assertIsNotNone(obesity)
        out_of_range = [m for m in obesity["markers"] if m["status"] in ("above_range", "below_range")]
        self.assertGreater(len(out_of_range), 0)

    def test_pathogens(self):
        pathogens = self.report["pathogens"]
        self.assertGreater(len(pathogens), 5)
        names = [p["name"] for p in pathogens]
        self.assertIn("Salmonella", names)
        self.assertIn("Campylobacter", names)

    def test_fungi(self):
        fungi = self.report["fungi"]
        self.assertGreater(len(fungi), 0)
        names = [f["name"] for f in fungi]
        self.assertTrue(any("candida" in n.lower() for n in names))

    def test_archaea(self):
        archaea = self.report["archaea"]
        self.assertGreater(len(archaea), 0)

    def test_viruses(self):
        viruses = self.report["viruses"]
        self.assertGreater(len(viruses), 0)


# ===========================================================================
# Test: Value parsing helpers
# ===========================================================================

class TestValueParsing(unittest.TestCase):

    def test_parse_percentage(self):
        from report_analyzer import parse_percentage
        self.assertAlmostEqual(parse_percentage("0.1782%"), 0.1782)
        self.assertAlmostEqual(parse_percentage("<0.101%"), 0.101)
        self.assertIsNone(parse_percentage("ND"))
        self.assertIsNone(parse_percentage(""))
        self.assertIsNone(parse_percentage(None))

    def test_parse_range(self):
        from report_analyzer import parse_range
        self.assertEqual(parse_range("0.014%-0.227%"), (0.014, 0.227))
        self.assertEqual(parse_range("<0.101%"), (0.0, 0.101))
        self.assertEqual(parse_range("ND"), (None, None))

    def test_compare_to_range(self):
        from report_analyzer import compare_to_range
        self.assertEqual(compare_to_range(0.5, 0.1, 1.0), "within_range")
        self.assertEqual(compare_to_range(2.0, 0.1, 1.0), "above_range")
        self.assertEqual(compare_to_range(0.01, 0.1, 1.0), "below_range")
        self.assertEqual(compare_to_range(None, 0.1, 1.0), "not_detected")
        self.assertEqual(compare_to_range(0.5, None, None), "no_reference")


# ===========================================================================
# Test: Structured Analysis Context
# ===========================================================================

class TestAnalysisContext(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from report_analyzer import extract_all_tables, build_full_analysis_context
        cls.report = extract_all_tables(SAMPLE_PDF)
        cls.context = build_full_analysis_context(cls.report)

    def test_context_not_empty(self):
        self.assertGreater(len(self.context), 1000)

    def test_context_has_patient(self):
        self.assertIn("Regina Levy", self.context)

    def test_context_has_diversity(self):
        self.assertIn("3.288", self.context)

    def test_context_has_fb_ratio(self):
        self.assertIn("2.73", self.context)

    def test_context_has_keystone(self):
        self.assertIn("KEYSTONE SPECIES", self.context)
        self.assertIn("missing", self.context.lower())

    def test_context_has_conditions(self):
        self.assertIn("CONDITION-SPECIFIC", self.context)
        self.assertIn("Depression", self.context)

    def test_context_has_out_of_range_flags(self):
        self.assertIn("OUT-OF-RANGE", self.context)
        self.assertIn("ABOVE_RANGE", self.context)

    def test_context_has_pathogens(self):
        self.assertIn("PATHOGENS", self.context)


# ===========================================================================
# Test: Gradio Event Handlers
# ===========================================================================

class TestGradioHandlers(unittest.TestCase):

    def test_extract_text_plain_string(self):
        from app import _extract_text
        self.assertEqual(_extract_text("hello"), "hello")

    def test_extract_text_gradio6_list(self):
        from app import _extract_text
        self.assertEqual(_extract_text([{"text": "hello", "type": "text"}]), "hello")

    def test_extract_text_empty(self):
        from app import _extract_text
        self.assertEqual(_extract_text([]), "")
        self.assertIsInstance(_extract_text(None), str)

    def test_user_message_normal(self):
        from app import user_message
        cleared, history = user_message("Hello", [])
        self.assertEqual(cleared, "")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "Hello")

    def test_user_message_empty(self):
        from app import user_message
        cleared, history = user_message("", [])
        self.assertEqual(len(history), 0)

    def test_user_message_none_history(self):
        from app import user_message
        cleared, history = user_message("Hello", None)
        self.assertIsInstance(history, list)
        self.assertEqual(len(history), 1)

    def test_chat_respond_empty_history(self):
        from app import chat_respond
        result = chat_respond([])
        self.assertIsInstance(result, list)

    def test_chat_respond_none_history(self):
        from app import chat_respond, _session
        old = _session["collection"]
        _session["collection"] = None
        try:
            result = chat_respond(None)
            self.assertIsInstance(result, list)
            self.assertIn("upload", result[-1]["content"].lower())
        finally:
            _session["collection"] = old

    def test_chat_respond_gradio6_format(self):
        from app import chat_respond, _session
        old = _session["collection"]
        _session["collection"] = None
        try:
            history = [{
                "role": "user",
                "content": [{"text": "test", "type": "text"}],
            }]
            result = chat_respond(history)
            self.assertGreater(len(result), 1)
        finally:
            _session["collection"] = old

    def test_process_upload_none(self):
        from app import process_upload
        result = process_upload(None)
        self.assertIn("upload", result.lower())

    def test_process_upload_valid(self):
        from app import process_upload, _session
        result = process_upload(SAMPLE_PDF)
        self.assertIn("successfully", result.lower())
        self.assertIsNotNone(_session["collection"])
        self.assertIsNotNone(_session["report"])
        self.assertGreater(len(_session["analysis_context"]), 0)
        # Check summary includes keystone info
        self.assertIn("missing", result.lower())
        self.assertIn("keystone", result.lower())


# ===========================================================================
# Test: Upload Summary
# ===========================================================================

class TestUploadSummary(unittest.TestCase):

    def test_summary_has_keystone_missing(self):
        """Key feedback: summary must show missing keystone species."""
        from app import process_upload
        result = process_upload(SAMPLE_PDF)
        self.assertIn("Ruminococcus bromii", result)
        self.assertIn("Clostridium butyricum", result)

    def test_summary_has_keystone_present(self):
        from app import process_upload
        result = process_upload(SAMPLE_PDF)
        self.assertIn("present", result.lower())

    def test_summary_has_conditions_flagged(self):
        from app import process_upload
        result = process_upload(SAMPLE_PDF)
        self.assertIn("conditions", result.lower())


# ===========================================================================
# Test: LLM Integration
# ===========================================================================

class TestLLMIntegration(unittest.TestCase):

    def setUp(self):
        from openai import OpenAI
        self.llm = OpenAI(base_url=VLLM_BASE_URL, api_key="not-needed")

    def test_llm_accessible(self):
        models = self.llm.models.list()
        self.assertTrue(any(m.id == MODEL_NAME for m in models.data))

    def test_think_stripping(self):
        raw = "<think>\nSome reasoning\n</think>\n\nActual answer."
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        self.assertEqual(cleaned, "Actual answer.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
