"""
Generate a PDF report from the E2E test results.
"""

from fpdf import FPDF
from datetime import datetime


class ReportPDF(FPDF):
    """Custom PDF with headers/footers."""

    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, "Genelio Bot - E2E Test Report", new_x="LMARGIN", new_y="NEXT", align="R")
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    # ── helpers ────────────────────────────────────────────────────────
    def section_title(self, title):
        self.ln(4)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(13, 148, 136)  # teal
        self.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(13, 148, 136)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def sub_title(self, title):
        self.ln(2)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(50, 50, 50)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body_text(self, text):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def bold_text(self, text):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5, text)
        self.ln(1)

    def badge(self, text, color):
        """Small colored badge."""
        r, g, b = color
        self.set_fill_color(r, g, b)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 8)
        w = self.get_string_width(text) + 6
        self.cell(w, 5.5, text, fill=True, new_x="END")
        self.set_text_color(30, 30, 30)

    def status_badge(self, status):
        colors = {
            "PASS": (34, 139, 34),
            "HIGH": (220, 53, 69),
            "MEDIUM": (255, 165, 0),
            "LOW": (108, 117, 125),
        }
        self.badge(status, colors.get(status, (100, 100, 100)))

    def table_row(self, cols, widths, bold=False, fill=False):
        self.set_font("Helvetica", "B" if bold else "", 8)
        if fill:
            self.set_fill_color(240, 240, 240)
        h = 6
        x_start = self.get_x()
        max_lines = 1
        # Calculate max lines needed
        for i, col in enumerate(cols):
            lines = self.multi_cell(widths[i], h, col, dry_run=True, output="LINES")
            max_lines = max(max_lines, len(lines))
        row_h = max_lines * h
        # Check page break
        if self.get_y() + row_h > 270:
            self.add_page()
        y_start = self.get_y()
        for i, col in enumerate(cols):
            self.set_xy(x_start + sum(widths[:i]), y_start)
            self.set_font("Helvetica", "B" if bold else "", 8)
            self.multi_cell(widths[i], h, col, border=1, fill=fill,
                           new_x="RIGHT", new_y="TOP", max_line_height=h)
        self.set_y(y_start + row_h)
        self.set_x(10)


def build_report():
    pdf = ReportPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ── Title page ─────────────────────────────────────────────────────
    pdf.ln(30)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(13, 148, 136)
    pdf.cell(0, 15, "Genelio Bot", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 16)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 10, "End-to-End Test Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, "3 PDFs x 10 Questions = 30 Total Tests", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    # Result box
    pdf.set_fill_color(34, 139, 34)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 12, "  RESULT: 30/30 ALL PASSED  ", align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(30, 30, 30)
    pdf.ln(10)

    # Executive summary
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, (
        "All 30 question-answer pairs returned substantive, well-formatted responses with markdown tables, "
        "status emojis, and actionable dietary advice. Response times ranged from 1.2s to 4.9s. "
        "The LLM + RAG pipeline works excellently. However, the structured report parser (report_analyzer.py) "
        "was built against the original sample report and does not fully generalize to these 3 new PDFs."
    ))

    # ── Test Configuration ─────────────────────────────────────────────
    pdf.add_page()
    pdf.section_title("1. Test Configuration")

    pdf.sub_title("Embedding Model")
    pdf.body_text("Model: nomic-ai/nomic-embed-text-v1.5 (768 dims, 8192 token context)")
    pdf.body_text("Chunk size: 1200 chars, Overlap: 200 chars")
    pdf.body_text("Task prefixes: 'search_document:' for indexing, 'search_query:' for retrieval")

    pdf.sub_title("LLM")
    pdf.body_text("Model: qwen3-30b via vLLM (localhost:8011)")
    pdf.body_text("Temperature: 0.3, Max tokens: 3072")

    pdf.sub_title("PDFs Tested")
    widths = [15, 55, 25, 25, 25, 25, 20]
    pdf.table_row(["#", "PDF Name", "Pages", "Chunks", "Conditions", "F/B Ratio", "Upload"], widths, bold=True, fill=True)
    pdf.table_row(["1", "GM AL C17 MR", "15", "35", "1", "6.279", "5.6s"], widths)
    pdf.table_row(["2", "GM AL C18 MS", "15", "34", "1", "3.514", "5.5s"], widths)
    pdf.table_row(["3", "Mail from Arshit Arora", "15", "35", "2", "2.922", "5.2s"], widths)

    pdf.sub_title("Questions Battery (10 questions per PDF)")
    questions = [
        "Q1: Is my gut microbiome healthy overall? Give me a quick summary.",
        "Q2: What is my Shannon Diversity score and what does it mean?",
        "Q3: Tell me about my F/B ratio - is it normal?",
        "Q4: Which keystone species am I missing and what foods can help?",
        "Q5: Tell me about my depression markers - are any out of range?",
        "Q6: What about my obesity markers?",
        "Q7: Do I have any IBD or inflammatory bowel markers that are flagged?",
        "Q8: Do I have any pathogens detected?",
        "Q9: Tell me about my fungi, archaea, and virus findings.",
        "Q10: What are the top 5 things I should do to improve my gut health?",
    ]
    for q in questions:
        pdf.body_text(q)

    # ── PDF 1 Results ──────────────────────────────────────────────────
    pdf.add_page()
    pdf.section_title("2. PDF 1: GM AL C17 MR")

    pdf.sub_title("Structured Parser Output")
    kv = [
        ("Patient Name", "Not parsed (shows 'Patient')"),
        ("Shannon Diversity", "Not extracted (empty dict) -- LLM found 4.742 via RAG"),
        ("F/B Ratio", "6.279 -- ABOVE RANGE (healthy: 0.14-0.76)"),
        ("Keystone Present", "17 (but all show ND status -- parsing issue)"),
        ("Keystone Missing", "0 (parser fails to detect missing species)"),
        ("Conditions Parsed", "1"),
        ("Pathogens", "0 structured (LLM answers from RAG)"),
        ("Fungi / Archaea / Viruses", "0 / 0 / 0 structured"),
    ]
    widths_kv = [55, 135]
    for k, v in kv:
        pdf.table_row([k, v], widths_kv)

    pdf.sub_title("Question-Answer Results")
    widths_qa = [12, 60, 14, 14, 90]
    pdf.table_row(["#", "Question", "Time", "Chars", "Response Summary"], widths_qa, bold=True, fill=True)

    qa1 = [
        ("Q1", "Overall health summary", "3.9s", "2251",
         "Diversity 4.742 above range, F/B 6.279 above range, no pathogens. Well-structured overview."),
        ("Q2", "Shannon Diversity", "1.7s", "1094",
         "Reports 4.742, above healthy range 2.34-4.5. Explains significance clearly."),
        ("Q3", "F/B Ratio", "1.9s", "1201",
         "6.279 above range. Links to obesity/metabolism. Dietary recommendations included."),
        ("Q4", "Keystone species missing", "2.1s", "1374",
         "Table with missing species + food recommendations per species."),
        ("Q5", "Depression markers", "2.7s", "1621",
         "Table: Holdemania 0.0212% flagged ABOVE RANGE. Others within/ND."),
        ("Q6", "Obesity markers", "2.5s", "1701",
         "Parabacteroides merdae within range. Clostridium hylemonae/scindens ND."),
        ("Q7", "IBD markers", "3.8s", "2145",
         "Akkermansia muciniphila & Eubacterium rectale flagged ABOVE RANGE."),
        ("Q8", "Pathogens", "1.8s", "1039",
         "None detected. Lists all checked: Salmonella, Vibrio, Campylobacter, etc."),
        ("Q9", "Fungi/Archaea/Viruses", "4.9s", "3072",
         "Methanobrevibacter smithii 2.9998%. Fungi/viruses detailed. Most comprehensive answer."),
        ("Q10", "Top 5 improvements", "3.1s", "2283",
         "Prebiotics, probiotics, fermented foods, fiber, stress management."),
    ]
    for q_num, question, t, chars, summary in qa1:
        pdf.table_row([q_num, question, t, chars, summary], widths_qa)

    # ── PDF 2 Results ──────────────────────────────────────────────────
    pdf.add_page()
    pdf.section_title("3. PDF 2: GM AL C18 MS")

    pdf.sub_title("Structured Parser Output")
    kv2 = [
        ("Patient Name", "Not parsed"),
        ("Shannon Diversity", "Not extracted -- LLM found 3.649 via RAG (within range)"),
        ("F/B Ratio", "3.514 -- ABOVE RANGE (healthy: 0.14-0.76)"),
        ("Keystone Present", "14"),
        ("Keystone Missing", "0 (parser issue)"),
        ("Conditions Parsed", "1 (with 1 flagged)"),
        ("Pathogens", "0 structured"),
        ("Fungi / Archaea / Viruses", "0 / 0 / 0 structured"),
    ]
    for k, v in kv2:
        pdf.table_row([k, v], widths_kv)

    pdf.sub_title("Question-Answer Results")
    pdf.table_row(["#", "Question", "Time", "Chars", "Response Summary"], widths_qa, bold=True, fill=True)

    qa2 = [
        ("Q1", "Overall health summary", "3.4s", "2098",
         "Diversity 3.649 within range. F/B 3.514 above. Flags Bifidobacterium adolescentis."),
        ("Q2", "Shannon Diversity", "1.6s", "1090",
         "3.649 within range (2.34-4.5). Good explanation of significance."),
        ("Q3", "F/B Ratio", "2.1s", "1379",
         "3.514 above range. Dietary suggestions for rebalancing."),
        ("Q4", "Keystone species missing", "2.5s", "1754",
         "Table: B. pseudolongum, B. stercoris, C. butyricum, Christensenella + foods."),
        ("Q5", "Depression markers", "4.1s", "2615",
         "Faecalibacterium 12.2451% ABOVE (ref 0.31-2.81%). Fusicatenibacter also flagged."),
        ("Q6", "Obesity markers", "2.5s", "1688",
         "Parabacteroides merdae within range. Others ND. Clean result."),
        ("Q7", "IBD markers", "4.4s", "2684",
         "Bifidobacterium longum ABOVE, Eubacterium rectale ABOVE range. Detailed table."),
        ("Q8", "Pathogens", "1.3s", "781",
         "None detected. All common pathogens checked and ND."),
        ("Q9", "Fungi/Archaea/Viruses", "2.9s", "1889",
         "Archaea: M. smithii 0.0192%. Fungi: all ND. Virus: Brigitvirus brigit detected."),
        ("Q10", "Top 5 improvements", "3.1s", "2246",
         "Tailored advice: prebiotic foods, probiotics, fiber, fermented foods, lifestyle."),
    ]
    for q_num, question, t, chars, summary in qa2:
        pdf.table_row([q_num, question, t, chars, summary], widths_qa)

    # ── PDF 3 Results ──────────────────────────────────────────────────
    pdf.add_page()
    pdf.section_title("4. PDF 3: Mail from Arshit Arora")

    pdf.sub_title("Structured Parser Output")
    kv3 = [
        ("Patient Name", "Not parsed"),
        ("Shannon Diversity", "Not extracted -- LLM found 4.491 via RAG (within range)"),
        ("F/B Ratio", "2.922 -- ABOVE RANGE (healthy: 0.14-0.76)"),
        ("Keystone Present", "12"),
        ("Keystone Missing", "0 (parser issue)"),
        ("Conditions Parsed", "2"),
        ("Pathogens", "0 structured"),
        ("Fungi / Archaea / Viruses", "0 / 0 / 0 structured"),
    ]
    for k, v in kv3:
        pdf.table_row([k, v], widths_kv)

    pdf.sub_title("Question-Answer Results")
    pdf.table_row(["#", "Question", "Time", "Chars", "Response Summary"], widths_qa, bold=True, fill=True)

    qa3 = [
        ("Q1", "Overall health summary", "2.3s", "1389",
         "Diversity 4.491 within range. F/B 2.922 above. Notes missing Faecalibacterium, Akkermansia."),
        ("Q2", "Shannon Diversity", "1.7s", "1073",
         "4.491 within range (2.34-4.5). Clear explanation."),
        ("Q3", "F/B Ratio", "1.8s", "1162",
         "2.922 above range. Links to dysbiosis, metabolic/inflammatory risk."),
        ("Q4", "Keystone species missing", "1.8s", "1234",
         "3 missing: B. pseudolongum, B. longum, C. butyricum with food table."),
        ("Q5", "Depression markers", "2.8s", "1790",
         "Faecalibacterium 3.6438% ABOVE (ref 0.31-2.81). Fusicatenibacter ABOVE. Eggerthella listed."),
        ("Q6", "Obesity markers", "2.4s", "1665",
         "Parabacteroides merdae within range. C. scindens 0.0017% detected."),
        ("Q7", "IBD markers", "3.5s", "2099",
         "Akkermansia 1.4001% ABOVE. E. rectale BELOW. F. prausnitzii ABOVE. Mixed flags."),
        ("Q8", "Pathogens", "1.2s", "650",
         "None detected. Shortest answer - clean result, no pathogens."),
        ("Q9", "Fungi/Archaea/Viruses", "3.4s", "2158",
         "M. smithii 1.3892%. Fungi all ND. Viruses detailed."),
        ("Q10", "Top 5 improvements", "4.3s", "3035",
         "Most detailed advice. Report-specific: missing Bifidobacterium, high F/B, fiber focus."),
    ]
    for q_num, question, t, chars, summary in qa3:
        pdf.table_row([q_num, question, t, chars, summary], widths_qa)

    # ── Issues Found ───────────────────────────────────────────────────
    pdf.add_page()
    pdf.section_title("5. Issues Found")

    widths_issues = [10, 22, 55, 103]
    pdf.table_row(["#", "Severity", "Issue", "Detail"], widths_issues, bold=True, fill=True)

    issues = [
        ("1", "HIGH", "Patient name not extracted",
         "All 3 PDFs show 'Patient' instead of actual name. Different layout than sample report."),
        ("2", "HIGH", "Shannon Diversity not structured",
         "diversity: {} for all 3 PDFs. Parser fails. LLM still answers correctly via RAG."),
        ("3", "MEDIUM", "Conditions under-parsed",
         "Only 1-2 conditions extracted vs. many in sample report. Table layout mismatch."),
        ("4", "MEDIUM", "Pathogens/Fungi/Archaea/Viruses = 0",
         "All show 0 in structured parse, but LLM correctly answers from RAG context."),
        ("5", "MEDIUM", "Keystone 'present' all show ND",
         "17 'present' but all show Not Detected emoji. Status/abundance parsing broken."),
        ("6", "LOW", "Keystone missing = 0 for all",
         "Parser reports 0 missing despite LLM finding missing species via RAG."),
    ]
    for num, severity, issue, detail in issues:
        pdf.table_row([num, severity, issue, detail], widths_issues)

    # ── Conclusions ────────────────────────────────────────────────────
    pdf.ln(6)
    pdf.section_title("6. Conclusions & Recommendations")

    pdf.sub_title("What Works Well")
    good = [
        "LLM + RAG pipeline produces excellent, substantive answers for all question types.",
        "Response quality: tables, status emojis, dietary advice, disclaimers all present.",
        "Response times: 1.2s - 4.9s per question (avg ~2.7s) -- good for local inference.",
        "Nomic embedding model handles table-heavy content well via RAG retrieval.",
        "F/B Ratio correctly parsed and flagged for all 3 PDFs.",
        "All 30/30 questions answered successfully with no errors.",
    ]
    for item in good:
        pdf.body_text(f"  [+]  {item}")

    pdf.sub_title("What Needs Fixing")
    bad = [
        "report_analyzer.py was built for 1 specific PDF layout and does not generalize.",
        "Patient name, Shannon Diversity, pathogens, fungi, archaea, viruses not extracted.",
        "Keystone species status/abundance parsing broken for new PDF formats.",
        "Condition tables only partially parsed (1-2 instead of full set).",
        "The bot relies almost entirely on RAG for these 3 PDFs -- structured context is sparse.",
    ]
    for item in bad:
        pdf.body_text(f"  [-]  {item}")

    pdf.sub_title("Recommended Next Steps")
    steps = [
        "1. Analyze table layouts in the 3 new PDFs vs. the sample to identify format differences.",
        "2. Update report_analyzer.py to handle multiple PDF layouts (template detection).",
        "3. Add patient name extraction for new formats.",
        "4. Fix Shannon Diversity extraction for new table structures.",
        "5. Fix keystone species abundance parsing and missing species detection.",
        "6. Add integration tests for all 4 PDF formats to prevent regressions.",
    ]
    for step in steps:
        pdf.body_text(step)

    # ── Save ───────────────────────────────────────────────────────────
    output_path = "/Users/user/dev/KWP/genelio-bot/E2E_Test_Report.pdf"
    pdf.output(output_path)
    return output_path


if __name__ == "__main__":
    path = build_report()
    print(f"PDF report generated: {path}")
