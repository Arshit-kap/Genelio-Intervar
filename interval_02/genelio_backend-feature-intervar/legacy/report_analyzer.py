"""
Structured report analyzer for gut microbiome PDF reports.

Parses tables from the PDF to extract:
- Patient info
- Keystone species (present + missing + dietary recommendations)
- Top organisms with abundance vs reference
- Condition-specific biomarkers with out-of-range flagging
- SCFA markers, pathogens, fungi, archaea, viruses
"""

import re
import pdfplumber


# ---------------------------------------------------------------------------
# Value parsing helpers
# ---------------------------------------------------------------------------

def parse_percentage(val: str) -> float | None:
    """Parse a percentage string like '0.1782%' or '<0.101%' into a float.
    Returns None for 'ND' or unparseable values."""
    if not val or val.strip().upper() == "ND":
        return None
    val = val.strip().replace("%", "").replace("<", "").replace(">", "")
    try:
        return float(val)
    except ValueError:
        return None


def parse_range(val: str) -> tuple[float | None, float | None]:
    """Parse a reference range like '0.014%-0.227%' or '<0.101%'.
    Returns (low, high). For '<X' returns (0, X). For 'ND' returns (None, None)."""
    if not val or val.strip().upper() == "ND":
        return None, None
    val = val.strip().replace("%", "")
    # Handle '<X' format (means 0 to X)
    m = re.match(r"<\s*([\d.]+)", val)
    if m:
        return 0.0, float(m.group(1))
    # Handle 'X-Y' or 'X%-Y%' format
    m = re.match(r"([\d.]+)\s*-\s*([\d.]+)", val)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def compare_to_range(abundance: float | None, low: float | None, high: float | None) -> str:
    """Compare abundance to healthy range. Returns status string."""
    if abundance is None:
        return "not_detected"
    if low is None and high is None:
        return "no_reference"
    if low is not None and abundance < low:
        return "below_range"
    if high is not None and abundance > high:
        return "above_range"
    return "within_range"


def status_emoji(status: str) -> str:
    if status == "above_range":
        return "🔴 ABOVE"
    elif status == "below_range":
        return "🟡 BELOW"
    elif status == "within_range":
        return "🟢 Normal"
    elif status == "not_detected":
        return "⚪ ND"
    return "—"


# ---------------------------------------------------------------------------
# Table extraction
# ---------------------------------------------------------------------------

def extract_all_tables(pdf_path: str) -> dict:
    """Extract and classify all tables from the PDF into a structured report."""
    report = {
        "patient": {},
        "keystone_present": [],
        "keystone_missing": [],
        "keystone_dietary": [],
        "top_organisms": [],
        "conditions": {},
        "scfa_markers": [],
        "tmao_markers": [],
        "pathogens": [],
        "fungi": [],
        "archaea": [],
        "viruses": [],
        "diversity": {},
        "fb_ratio": {},
        "full_text": "",
    }

    with pdfplumber.open(pdf_path) as pdf:
        all_text_parts = []

        for page_idx in range(len(pdf.pages)):
            page = pdf.pages[page_idx]
            page_num = page_idx + 1
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            all_text_parts.append(text)

            # --- Patient info (page 2) ---
            if page_num == 2:
                report["patient"] = _parse_patient_info(tables, text)

            # --- Diversity (page 5) ---
            if "diversity" in text.lower() and "shannon" in text.lower():
                m = re.search(r"diversity score\s*([\d.]+)", text, re.IGNORECASE)
                if m:
                    score = float(m.group(1))
                    report["diversity"] = {
                        "score": score,
                        "range": "2.34-4.5",
                        "status": "within_range" if 2.34 <= score <= 4.5 else (
                            "below_range" if score < 2.34 else "above_range"
                        ),
                    }

            # --- F/B ratio (page 11) ---
            if "f/b" in text.lower() and "firmicutes" in text.lower():
                m = re.search(r"F/B.*?is.*?([\d.]+)", text)
                if m:
                    ratio = float(m.group(1))
                    report["fb_ratio"] = {
                        "ratio": ratio,
                        "range": "0.14-0.76",
                        "status": "within_range" if 0.14 <= ratio <= 0.76 else (
                            "below_range" if ratio < 0.14 else "above_range"
                        ),
                    }

            # --- Top organisms (pages 7-8) ---
            if page_num in (7, 8) and tables:
                for table in tables:
                    organisms = _parse_organism_table(table, page_num)
                    report["top_organisms"].extend(organisms)

            # --- Keystone species (pages 9-10) ---
            if page_num in (9, 10):
                for table in tables:
                    header = _get_table_header(table)
                    if "missing" in str(header).lower() or (
                        table and len(table[0]) >= 4 and "Whole Grain" in str(table[0])
                    ):
                        # Dietary recommendation table
                        report["keystone_dietary"] = _parse_dietary_table(table)
                    elif header and any("abundance" in str(h).lower() for h in header if h):
                        organisms = _parse_organism_table(table, page_num)
                        report["keystone_present"].extend(organisms)

                # Extract missing keystone from text
                m = re.search(
                    r"missing keystone species are\s+(.+?)\.",
                    text, re.IGNORECASE | re.DOTALL,
                )
                if m:
                    missing_text = re.sub(r"\s+", " ", m.group(1).strip())
                    report["keystone_missing"] = [
                        s.strip() for s in missing_text.split(",") if s.strip()
                    ]

            # --- Condition-specific biomarkers (pages 13-18) ---
            if page_num in range(13, 19):
                _parse_condition_page(text, tables, page_num, report)

            # --- Pathogens, Fungi, Archaea, Viruses (pages 19-20) ---
            if page_num in (19, 20):
                _parse_special_markers(text, tables, page_num, report)

            # --- SCFA markers (pages 11-12) ---
            if page_num in (11, 12):
                for table in tables:
                    header = _get_table_header(table)
                    if header and any("SFCA" in str(h) or "SCFA" in str(h) for h in header if h):
                        for row in table[1:]:
                            if row and len(row) >= 4:
                                marker = {
                                    "type": row[0] if row[0] else "",
                                    "name": (row[1] or "").split("\n")[0].strip(),
                                    "abundance": row[2],
                                    "reference": row[3],
                                }
                                if marker["name"]:
                                    report["scfa_markers"].append(marker)

                    # TMAO markers
                    if header and any("Microbioal" in str(h) or "Microbial" in str(h) for h in header if h):
                        for row in table[1:]:
                            if row and len(row) >= 3:
                                report["tmao_markers"].append({
                                    "name": (row[0] or "").split("\n")[0].strip(),
                                    "abundance": row[1],
                                    "reference": row[2],
                                })

        report["full_text"] = "\n".join(all_text_parts)

    # --- Post-processing: merge keystone species from top organisms ---
    # The PDF marks keystone species in bold within the top-10 table, but
    # pdfplumber can't detect bold in table cells. Instead, we:
    # 1) Check significance text for "keystone" keyword
    # 2) Extract the total/present count from the report text
    keystone_names = {org["name"] for org in report["keystone_present"]}
    for org in report["top_organisms"]:
        if "keystone" in org.get("significance", "").lower():
            if org["name"] not in keystone_names:
                org_copy = dict(org)
                org_copy["source"] = "top_organisms"
                report["keystone_present"].append(org_copy)
                keystone_names.add(org["name"])

    # Extract total/present count from text
    m = re.search(
        r"Among\s+(\d+)\s+keystone species.*?you have\s+(\d+)\s+keystone",
        report["full_text"], re.IGNORECASE | re.DOTALL,
    )
    if m:
        report["keystone_total"] = int(m.group(1))
        report["keystone_present_count"] = int(m.group(2))
    else:
        report["keystone_total"] = len(report["keystone_present"]) + len(report["keystone_missing"])
        report["keystone_present_count"] = len(report["keystone_present"])

    return report


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _get_table_header(table: list) -> list | None:
    if table and len(table) > 0:
        return table[0]
    return None


def _parse_patient_info(tables: list, text: str) -> dict:
    info = {}
    for table in tables:
        for row in table:
            if not row:
                continue
            for i in range(0, len(row) - 1, 2):
                key = (row[i] or "").replace(":", "").strip()
                val = (row[i + 1] or "").strip() if i + 1 < len(row) else ""
                if key:
                    info[key] = val
    return info


def _parse_organism_table(table: list, page_num: int) -> list:
    """Parse a table with columns: Name, Abundance, Reference, Significance."""
    organisms = []
    if not table or len(table) < 2:
        return organisms
    for row in table[1:]:  # Skip header
        if not row or len(row) < 3:
            continue
        name_raw = (row[0] or "").strip()
        name = name_raw.split("\n")[0].strip()  # First line is the species name
        if not name or "scientific" in name.lower():
            continue

        abundance_str = (row[1] or "").strip()
        reference_str = (row[2] or "").strip()
        significance = (row[3] if len(row) > 3 else "") or ""

        abundance = parse_percentage(abundance_str)
        low, high = parse_range(reference_str)
        status = compare_to_range(abundance, low, high)

        organisms.append({
            "name": name,
            "full_info": name_raw,
            "abundance": abundance_str,
            "abundance_val": abundance,
            "reference": reference_str,
            "ref_low": low,
            "ref_high": high,
            "status": status,
            "significance": significance.replace("\n", " "),
            "page": page_num,
        })
    return organisms


def _parse_biomarker_table(table: list, page_num: int) -> list:
    """Parse a condition biomarker table: Name, Abundance, Reference."""
    markers = []
    if not table or len(table) < 2:
        return markers
    for row in table[1:]:
        if not row or len(row) < 3:
            continue
        name = (row[0] or "").split("\n")[0].strip()
        if not name or "microbioal" in name.lower() or "microbial" in name.lower():
            continue

        abundance_str = (row[1] or "").strip()
        reference_str = (row[2] or "").strip()
        abundance = parse_percentage(abundance_str)
        low, high = parse_range(reference_str)
        status = compare_to_range(abundance, low, high)

        markers.append({
            "name": name,
            "abundance": abundance_str,
            "abundance_val": abundance,
            "reference": reference_str,
            "ref_low": low,
            "ref_high": high,
            "status": status,
            "page": page_num,
        })
    return markers


def _parse_dietary_table(table: list) -> list:
    """Parse the missing keystone dietary recommendation table."""
    recs = []
    if not table or len(table) < 2:
        return recs
    headers = [str(h or "").strip() for h in table[0]]
    for row in table[1:]:
        if not row or not row[0]:
            continue
        entry = {"species": row[0].replace("\n", " ").strip()}
        for i, h in enumerate(headers[1:], 1):
            if i < len(row) and row[i]:
                entry[h.lower().replace(" ", "_")] = row[i].replace("\n", ", ").strip()
        recs.append(entry)
    return recs


def _parse_condition_page(text: str, tables: list, page_num: int, report: dict):
    """Parse condition-specific biomarker pages (13-18)."""
    lines = text.split("\n")

    # Find all condition headings and their associated "direction" hints
    sections = []
    current_condition = None
    current_direction = None  # "reduced" or "increased" or "higher"

    for line in lines:
        line_lower = line.lower().strip()

        # New condition heading
        m = re.match(
            r"microbial?\s*(?:bio)?markers?\s+for\s+(.+)",
            line_lower,
        )
        if m:
            current_condition = m.group(1).strip().rstrip(".")
            current_direction = None
            continue

        # Direction hint
        if current_condition:
            if any(kw in line_lower for kw in ["reduced level", "depletion"]):
                current_direction = "reduced"
                sections.append((current_condition, current_direction))
            elif any(kw in line_lower for kw in ["increased level", "higher abundance", "higher level"]):
                current_direction = "increased"
                sections.append((current_condition, current_direction))

    # Now associate tables with sections
    # Tables appear in order, matching the section order
    table_idx = 0
    for condition, direction in sections:
        if table_idx >= len(tables):
            break

        markers = _parse_biomarker_table(tables[table_idx], page_num)
        table_idx += 1

        if not markers:
            continue

        # Add direction and flag out-of-range
        for m in markers:
            m["direction"] = direction
            m["condition"] = condition

        cond_key = condition.lower()
        if cond_key not in report["conditions"]:
            report["conditions"][cond_key] = {"name": condition, "markers": []}
        report["conditions"][cond_key]["markers"].extend(markers)


def _parse_special_markers(text: str, tables: list, page_num: int, report: dict):
    """Parse pathogen, fungi, archaea, virus tables on pages 18-20.

    Strategy: find where each category heading appears in the text, then
    map each table's *first data row name* to figure out which section it
    belongs to by checking whether that marker name appears after the heading.
    """
    lines = text.split("\n")

    # Build ordered list of (line_index, category) for all headings
    heading_positions = []
    for i, line in enumerate(lines):
        ll = line.lower()
        if "marker" in ll or "linked to" in ll:
            if "pathogen" in ll:
                heading_positions.append((i, "pathogens"))
            elif "fungi" in ll or "fungal" in ll:
                heading_positions.append((i, "fungi"))
            elif "archea" in ll or "archaea" in ll:
                heading_positions.append((i, "archaea"))
            elif "virus" in ll:
                heading_positions.append((i, "viruses"))
            elif "cancer" in ll:
                heading_positions.append((i, "cancer"))
            elif "hypertension" in ll:
                heading_positions.append((i, "hypertension"))
            elif "thryoidism" in ll or "thyroidism" in ll:
                heading_positions.append((i, "thyroidism"))

    # For each table, find its first data marker name in the page text,
    # then use that position to determine which heading it falls under.
    for table in tables:
        markers = _parse_biomarker_table(table, page_num)
        if not markers:
            continue

        # Find where the first marker name appears in the text
        first_name = markers[0]["name"]
        marker_line_idx = None
        for i, line in enumerate(lines):
            if first_name.lower() in line.lower():
                marker_line_idx = i
                break

        # Determine category: the most recent heading before this marker
        cat = None
        if marker_line_idx is not None and heading_positions:
            for h_idx, h_cat in reversed(heading_positions):
                if h_idx < marker_line_idx:
                    cat = h_cat
                    break

        if cat is None:
            # Fallback: if first heading on page comes after table, use previous page context
            if heading_positions:
                cat = heading_positions[0][1]
            else:
                continue

        if cat in ("pathogens", "fungi", "archaea", "viruses"):
            report[cat].extend(markers)
        else:
            cond_key = cat.lower()
            if cond_key not in report["conditions"]:
                report["conditions"][cond_key] = {"name": cat.title(), "markers": []}
            report["conditions"][cond_key]["markers"].extend(markers)


# ---------------------------------------------------------------------------
# Structured summary builders
# ---------------------------------------------------------------------------

def build_structured_summary(report: dict) -> str:
    """Build a comprehensive markdown summary from the structured report."""
    parts = []

    # Patient info
    p = report.get("patient", {})
    name = p.get("Name", "Patient")
    parts.append(f"## Report Summary for {name}\n")

    # Diversity
    d = report.get("diversity", {})
    if d:
        emoji = "🟢" if d["status"] == "within_range" else "🔴"
        parts.append(f"**Shannon Diversity Index**: {d['score']} {emoji} (healthy: {d['range']})")

    # F/B ratio
    fb = report.get("fb_ratio", {})
    if fb:
        emoji = "🟢" if fb["status"] == "within_range" else "🔴"
        parts.append(f"**F/B Ratio**: {fb['ratio']} {emoji} (healthy: {fb['range']})")

    # Keystone species
    present = report.get("keystone_present", [])
    missing = report.get("keystone_missing", [])
    if present or missing:
        parts.append(f"\n### Keystone Species ({len(present)} present, {len(missing)} missing)")
        if present:
            parts.append("**Present:**")
            for org in present:
                parts.append(f"- {org['name']} — {org['abundance']} {status_emoji(org['status'])} (ref: {org['reference']})")
        if missing:
            parts.append("**Missing:**")
            for sp in missing:
                parts.append(f"- ❌ {sp}")
        dietary = report.get("keystone_dietary", [])
        if dietary:
            parts.append("\n**Dietary tips for missing species:**")
            for rec in dietary:
                foods = []
                for k, v in rec.items():
                    if k != "species" and v:
                        foods.append(v)
                parts.append(f"- **{rec['species']}**: {'; '.join(foods)}")

    # Condition flags — only show conditions with out-of-range markers
    conditions = report.get("conditions", {})
    flagged_conditions = []
    for cond_key, cond_data in conditions.items():
        out_of_range = [
            m for m in cond_data["markers"]
            if m["status"] in ("above_range", "below_range")
        ]
        if out_of_range:
            flagged_conditions.append((cond_data["name"], out_of_range))

    if flagged_conditions:
        parts.append("\n### ⚠️ Conditions with Out-of-Range Markers")
        for cond_name, markers in flagged_conditions:
            parts.append(f"\n**{cond_name.title()}** (Page {markers[0]['page']}):")
            for m in markers:
                direction = m.get("direction", "")
                dir_label = f" [{direction}]" if direction else ""
                parts.append(
                    f"- {m['name']}: {m['abundance']} {status_emoji(m['status'])}"
                    f" (ref: {m['reference']}){dir_label}"
                )

    return "\n".join(parts)


def build_condition_context(report: dict, condition_name: str) -> str:
    """Build detailed context for a specific condition query."""
    conditions = report.get("conditions", {})

    # Find matching condition
    matches = []
    for cond_key, cond_data in conditions.items():
        if condition_name.lower() in cond_key.lower():
            matches.append(cond_data)

    if not matches:
        return ""

    parts = []
    for cond_data in matches:
        parts.append(f"## {cond_data['name'].title()} — Biomarker Analysis\n")
        for m in cond_data["markers"]:
            direction = m.get("direction", "")
            dir_context = ""
            if direction == "reduced":
                dir_context = "(reduced levels linked to this condition)"
            elif direction == "increased":
                dir_context = "(higher levels linked to this condition)"

            parts.append(
                f"- **{m['name']}**: abundance={m['abundance']}, "
                f"reference={m['reference']}, "
                f"status={status_emoji(m['status'])} {dir_context}"
            )
    return "\n".join(parts)


def build_full_analysis_context(report: dict) -> str:
    """Build the complete structured analysis that gets injected into every
    LLM query as pre-analyzed context. This is the 'brain' of the system.

    IMPORTANT: Keystone species and SCFA markers are kept in clearly separate
    sections so the LLM never confuses them.
    """
    parts = []

    # ---- Overview ----
    p = report.get("patient", {})
    parts.append("# STRUCTURED REPORT ANALYSIS")
    parts.append(f"Patient: {p.get('Name', 'Unknown')}")

    d = report.get("diversity", {})
    if d:
        parts.append(f"Shannon Diversity: {d['score']} (range: {d['range']}) -> {d['status']}")

    fb = report.get("fb_ratio", {})
    if fb:
        parts.append(f"F/B Ratio: {fb['ratio']} (range: {fb['range']}) -> {fb['status']}")

    # ---- Top 10 most abundant organisms ----
    top = report.get("top_organisms", [])
    if top:
        parts.append("\n## TOP 10 MOST ABUNDANT ORGANISMS (not all are keystone)")
        for org in top:
            parts.append(
                f"- {org['name']}: {org['abundance']} (ref: {org['reference']}) "
                f"-> {org['status']}. {org.get('significance', '')[:200]}"
            )

    # ---- Keystone species (DEFINITIVE LIST) ----
    present = report.get("keystone_present", [])
    missing = report.get("keystone_missing", [])
    total = report.get("keystone_total", len(present) + len(missing))
    present_count = report.get("keystone_present_count", len(present))
    parts.append(
        f"\n## KEYSTONE SPECIES (DEFINITIVE LIST)"
        f"\nThe report tracks {total} keystone species. "
        f"{present_count} are present in this gut, {len(missing)} are missing."
    )
    parts.append(
        "\nIMPORTANT: Keystone species are specific bacterial species — "
        "NOT SCFAs (Short-Chain Fatty Acids like Acetate, Propionate, Butyrate). "
        "Do NOT list SCFA types as keystone species."
    )
    if present:
        parts.append(f"\nPresent keystone species ({len(present)} identified):")
        for org in present:
            source = f" [from top 10]" if org.get("source") == "top_organisms" else ""
            parts.append(
                f"- {org['name']}: abundance={org['abundance']}, ref={org['reference']}, "
                f"status={org['status']}{source}. {org.get('significance', '')[:150]}"
            )
    if missing:
        parts.append(f"\nMissing keystone species ({len(missing)}):")
        for sp in missing:
            parts.append(f"- ❌ {sp} (NOT detected in the gut)")
    dietary = report.get("keystone_dietary", [])
    if dietary:
        parts.append("\nDietary recommendations to restore missing keystone species:")
        for rec in dietary:
            foods = [f"{k}: {v}" for k, v in rec.items() if k != "species" and v]
            parts.append(f"  {rec['species']}: {'; '.join(foods)}")

    # ---- SCFA markers (SEPARATE from keystone — these are metabolite types) ----
    scfa = report.get("scfa_markers", [])
    if scfa:
        parts.append(
            "\n## SCFA (SHORT-CHAIN FATTY ACID) PRODUCING BACTERIA"
            "\nNote: Acetate, Propionate, Butyrate are SCFA *types* (metabolites), "
            "NOT species names. Below are the bacteria that produce each SCFA type:"
        )
        current_type = ""
        for s in scfa:
            if s["type"] and s["type"] != current_type:
                current_type = s["type"]
                parts.append(f"\n  {current_type} producers:")
            parts.append(
                f"    - {s['name']}: {s['abundance']} (ref: {s['reference']})"
            )

    # ---- Conditions ----
    conditions = report.get("conditions", {})
    if conditions:
        parts.append("\n## CONDITION-SPECIFIC BIOMARKER ANALYSIS")
        for cond_key, cond_data in conditions.items():
            markers = cond_data["markers"]
            out_of_range = [m for m in markers if m["status"] in ("above_range", "below_range")]
            within = [m for m in markers if m["status"] == "within_range"]
            nd = [m for m in markers if m["status"] == "not_detected"]

            parts.append(f"\n### {cond_data['name'].title()}")
            if out_of_range:
                parts.append(f"  ⚠️ OUT-OF-RANGE markers ({len(out_of_range)}):")
                for m in out_of_range:
                    direction = m.get("direction", "")
                    parts.append(
                        f"    - {m['name']}: {m['abundance']} vs ref {m['reference']} "
                        f"-> {m['status'].upper()} [report says {direction} levels linked to condition]"
                    )
            if within:
                parts.append(f"  ✅ Within-range markers ({len(within)}):")
                for m in within:
                    parts.append(f"    - {m['name']}: {m['abundance']} (ref: {m['reference']})")
            if nd:
                parts.append(f"  ⚪ Not detected ({len(nd)}): {', '.join(m['name'] for m in nd)}")

    # ---- Pathogens ----
    pathogens = report.get("pathogens", [])
    if pathogens:
        parts.append("\n## PATHOGENS")
        detected = [pt for pt in pathogens if pt["status"] != "not_detected"]
        not_detected = [pt for pt in pathogens if pt["status"] == "not_detected"]
        if detected:
            parts.append("⚠️ DETECTED:")
            for pt in detected:
                parts.append(f"  - {pt['name']}: {pt['abundance']} (ref: {pt['reference']}) -> {pt['status']}")
        if not_detected:
            parts.append(f"✅ Not detected: {', '.join(pt['name'] for pt in not_detected)}")

    return "\n".join(parts)
