"""HPO resolution — symptom phrase → HPO term(s) → gene set.

Backed by two bundled, gzipped TSVs distilled from the official HPO
release (``phenotype_to_genes.txt``):

- ``data/hpo_terms.tsv.gz``     : ``hpo_id → hpo_name``    (~12k rows)
- ``data/hpo_to_genes.tsv.gz``  : ``hpo_id → gene_symbol`` (~910k rows)

The loader keeps both lookup directions in memory (~50 MB) so resolution
is O(1) per symptom. Lazy-imported via ``_index()`` so Django boot stays
cheap; the heavy load only happens on the first agentic chat call.

The resolver supports three matching strategies in order of confidence:
1. Exact name match (case-insensitive)
2. Substring / phrase match against term names
3. Fallback: empty result with confidence=0 — caller asks the user to
   clarify (per §7.1 of the integration spec)

LLM-assisted fuzzy expansion is deliberately out of scope for v1 —
keeping the resolver deterministic makes the orchestrator easier to
reason about and test. We can layer in an LLM fallback later.
"""
from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_TERMS_FILE = _DATA_DIR / "hpo_terms.tsv.gz"
_MAPPINGS_FILE = _DATA_DIR / "hpo_to_genes.tsv.gz"


@dataclass(frozen=True)
class HPOTerm:
    """A resolved HPO match for a single lay-language symptom phrase."""
    hpo_id: str
    name: str
    confidence: float
    matched_via: str  # "exact" | "substring" | "fallback"
    genes: tuple[str, ...] = field(default_factory=tuple)

    # Cap genes exposed to the LLM / UI to a clinically actionable
    # shortlist (per product spec). The full set is still used internally
    # to compute the variant intersection — this only caps what we show
    # the user / answer LLM, so the response stays focused.
    GENE_DISPLAY_CAP = 10

    def as_dict(self) -> dict:
        return {
            "hpo_id": self.hpo_id,
            "name": self.name,
            "confidence": self.confidence,
            "matched_via": self.matched_via,
            "gene_count": len(self.genes),
            "genes_sample": list(self.genes[:self.GENE_DISPLAY_CAP]),
        }


@dataclass
class _Index:
    """In-memory HPO index, lazily built once per process."""
    # hpo_id -> canonical name
    id_to_name: dict[str, str]
    # lowercased name -> hpo_id (exact match)
    name_to_id: dict[str, str]
    # hpo_id -> tuple of gene symbols
    id_to_genes: dict[str, tuple[str, ...]]
    # All lowercased names, for substring matching
    name_lower_list: list[tuple[str, str]]  # (lowered_name, hpo_id)


@lru_cache(maxsize=1)
def _index() -> _Index:
    """Build the in-memory HPO index (idempotent)."""
    if not _TERMS_FILE.exists() or not _MAPPINGS_FILE.exists():
        raise FileNotFoundError(
            f"HPO data files missing under {_DATA_DIR}. Run the "
            "refresh management command to download them."
        )

    log.info("Loading HPO index from %s", _DATA_DIR)
    id_to_name: dict[str, str] = {}
    with gzip.open(_TERMS_FILE, "rt", encoding="utf-8") as fh:
        next(fh)  # header
        for line in fh:
            hp, name = line.rstrip("\n").split("\t")
            id_to_name[hp] = name

    id_to_genes_lists: dict[str, list[str]] = {}
    with gzip.open(_MAPPINGS_FILE, "rt", encoding="utf-8") as fh:
        next(fh)  # header
        for line in fh:
            hp, gene = line.rstrip("\n").split("\t")
            id_to_genes_lists.setdefault(hp, []).append(gene)
    id_to_genes: dict[str, tuple[str, ...]] = {
        hp: tuple(sorted(set(genes))) for hp, genes in id_to_genes_lists.items()
    }

    name_to_id: dict[str, str] = {
        name.lower(): hp for hp, name in id_to_name.items()
    }
    name_lower_list = sorted(
        ((name.lower(), hp) for hp, name in id_to_name.items()),
        key=lambda x: -len(x[0]),  # prefer longer matches
    )

    log.info(
        "HPO index: %d terms, %d (term→gene) pairs across %d unique genes",
        len(id_to_name), sum(len(g) for g in id_to_genes.values()),
        len({g for genes in id_to_genes.values() for g in genes}),
    )
    return _Index(
        id_to_name=id_to_name,
        name_to_id=name_to_id,
        id_to_genes=id_to_genes,
        name_lower_list=name_lower_list,
    )


def _normalize(phrase: str) -> str:
    """Lowercase + collapse whitespace + strip leading articles + fillers.

    Multi-word fillers must be listed BEFORE single-token ones so the
    alternation prefers the longer match — ``re`` is leftmost+leftish,
    so ``"i have"`` must come earlier than bare ``"i"``.
    """
    p = re.sub(r"\s+", " ", phrase.strip().lower())
    pattern = re.compile(
        r"^("
        r"i\s+sometimes\s+(get|feel|have)|"
        r"i\s+(have|get|am|feel|sometimes)|"
        r"there\s+(is|are)|"
        r"a|an|the|some|my|i|got|gets|getting"
        r")\s+"
    )
    while True:
        new = pattern.sub("", p)
        if new == p:
            break
        p = new
    p = p.rstrip(".,!?;:")
    return p


# A small synonym dictionary for the most common lay → HPO mappings. Keeps
# the demo robust without depending on an LLM fuzzy-match call. Each entry
# is (lay term lowercased) → (canonical HPO name in our index).
_LAY_SYNONYMS: dict[str, str] = {
    # Bleeding / bruising
    "easy bruising": "Bruising susceptibility",
    "bruise easily": "Bruising susceptibility",
    "bruising easily": "Bruising susceptibility",
    "bruise really easily": "Bruising susceptibility",
    "bruise a lot": "Bruising susceptibility",
    "easily bruised": "Bruising susceptibility",
    "prone to bruising": "Bruising susceptibility",
    "bleed easily": "Abnormal bleeding",
    "heavy bleeding": "Abnormal bleeding",
    "bleeding a lot": "Abnormal bleeding",
    "nose bleeds": "Epistaxis",
    "nosebleeds": "Epistaxis",
    # Vision
    "night blindness": "Nyctalopia",
    "bad night vision": "Nyctalopia",
    "trouble seeing at night": "Nyctalopia",
    "can't see well at night": "Nyctalopia",
    "blurry vision": "Blurred vision",
    # Muscle
    "weak muscles": "Muscle weakness",
    "muscle weakness": "Muscle weakness",
    "weak legs": "Lower limb muscle weakness",
    "trouble walking": "Gait disturbance",
    "difficulty walking": "Gait disturbance",
    "hard to walk": "Gait disturbance",
    "walking problems": "Gait disturbance",
    "muscle pain": "Myalgia",
    "muscle pains": "Myalgia",
    "muscle aches": "Myalgia",
    "sore muscles": "Myalgia",
    "myalgia": "Myalgia",
    "muscle cramps": "Muscle cramps",
    "cramping muscles": "Muscle cramps",
    # Pain / joints
    "joint pain": "Arthralgia",
    "joints hurt": "Arthralgia",
    "abdominal pain": "Abdominal pain",
    "stomach pain": "Abdominal pain",
    "belly pain": "Abdominal pain",
    "fatty food intolerance": "Steatorrhea",
    # Hearing
    "hearing loss": "Hearing impairment",
    "hearing problems": "Hearing impairment",
    "hard of hearing": "Hearing impairment",
    "deafness": "Hearing impairment",
    "trouble hearing": "Hearing impairment",
    # Seizures
    "seizure": "Seizure",
    "seizures": "Seizure",
    "epilepsy": "Seizure",
    "convulsions": "Seizure",
    # Energy / sleep
    "lethargic": "Lethargy",
    "lethargy": "Lethargy",
    "feeling drained": "Lethargy",
    "no energy": "Fatigue",
    "low energy": "Fatigue",
    "always tired": "Fatigue",
    # Developmental
    "developmental delay": "Global developmental delay",
    "slow development": "Global developmental delay",
    # Cognitive / neuro
    "headache": "Headache",
    "headaches": "Headache",
    "bad headaches": "Headache",
    "migraine": "Migraine",
    "migraines": "Migraine",
    "brain fog": "Cognitive impairment",
    "feeling foggy": "Cognitive impairment",
    "memory problems": "Memory impairment",
    "numbness": "Paresthesia",
    "fingers go numb": "Paresthesia",
    # Cardiac
    "heart palpitations": "Palpitations",
    "irregular heartbeat": "Arrhythmia",
    # Fatigue
    "tired easily": "Fatigue",
    "fatigue": "Fatigue",
    "exhausted": "Fatigue",
    # Liver
    "yellow skin": "Jaundice",
    "yellow eyes": "Jaundice",
    # Movement disorders
    "tremor": "Tremor",
    "shaking hands": "Tremor",
    # Single-word organ / body-system queries. These are intentionally
    # broad — they route to the relevant HP "Abnormality of …" root
    # term so a patient saying just "lung" or "heart" lands on a
    # sensible respiratory / cardiac category rather than triggering
    # the substring fallback (which used to mis-route "lung" →
    # "Madelung-like forearm deformities").
    "lung": "Abnormal lung morphology",
    "lungs": "Abnormal lung morphology",
    "breathing": "Abnormal lung morphology",
    "respiratory": "Abnormal lung morphology",
    "heart": "Abnormal heart morphology",
    "cardiac": "Abnormal heart morphology",
    "kidney": "Abnormality of the kidney",
    "kidneys": "Abnormality of the kidney",
    "renal": "Abnormality of the kidney",
    "liver": "Abnormality of the liver",
    "hepatic": "Abnormality of the liver",
    "brain": "Abnormality of brain morphology",
    "skin": "Abnormality of the skin",
    "bones": "Abnormality of the skeletal system",
    "skeleton": "Abnormality of the skeletal system",
    "eye": "Abnormality of the eye",
    "eyes": "Abnormality of the eye",
    "vision": "Abnormality of vision",
    "ear": "Abnormality of the ear",
    "ears": "Abnormality of the ear",
    "muscle": "Abnormality of the musculature",
    "muscles": "Abnormality of the musculature",
    "gut": "Abnormality of the digestive system",
    "stomach": "Abnormality of the digestive system",
    "intestine": "Abnormality of the digestive system",
    "blood": "Abnormality of blood and blood-forming tissues",
    "thyroid": "Abnormality of the thyroid gland",
}


def resolve(phrase: str) -> HPOTerm:
    """Resolve a single lay-language symptom phrase to an HPO term + genes.

    Returns a sentinel term with ``hpo_id == ""`` and ``confidence == 0.0``
    when no match is found. The caller (orchestrator) should then ask
    the user to clarify rather than fabricate an HPO mapping.
    """
    idx = _index()
    norm = _normalize(phrase)

    # 1. Synonym dictionary (highest confidence — lay → canonical)
    if norm in _LAY_SYNONYMS:
        canonical = _LAY_SYNONYMS[norm]
        hp = idx.name_to_id.get(canonical.lower())
        if hp:
            return HPOTerm(
                hpo_id=hp, name=idx.id_to_name[hp],
                confidence=0.95, matched_via="synonym",
                genes=idx.id_to_genes.get(hp, ()),
            )

    # 2. Exact (case-insensitive) name match against canonical HPO names
    hp = idx.name_to_id.get(norm)
    if hp:
        return HPOTerm(
            hpo_id=hp, name=idx.id_to_name[hp],
            confidence=0.90, matched_via="exact",
            genes=idx.id_to_genes.get(hp, ()),
        )

    # 3. Substring match — require word-level overlap with a meaningful
    #    content word (≥4 chars) on at least one side, and minimum
    #    overlap-ratio of 0.4. This prevents toy matches like "All" → norm.
    SHORT_STOP = {"the", "and", "for", "with", "have", "has", "had",
                  "this", "that", "very", "really", "much", "some",
                  "any", "all", "lot", "are", "was", "were"}
    norm_words = [w for w in re.split(r"[^a-z0-9]+", norm) if w and w not in SHORT_STOP and len(w) >= 4]
    if not norm_words:
        return HPOTerm(hpo_id="", name="", confidence=0.0,
                       matched_via="fallback", genes=())

    # The substring-fallback used to permit ``w in cw`` (substring) in
    # addition to exact-word membership. That's how "lung" (4 chars) was
    # matching "Madelung-like forearm deformities" via the "lung" ⊂
    # "madelung" inclusion — Saurabh's bioinformatician report flagged
    # exactly this. Tighten the fallback to require **word-boundary
    # alignment**: the input word must match a tokenised word of the
    # candidate term exactly, or (for ≥5-char input words) it may match
    # as a prefix/suffix of a tokenised word. Mid-word substring matches
    # are no longer accepted.
    best: tuple[str, str, float] | None = None  # (hp_id, matched_name, score)
    for cand_lower, cand_hp in idx.name_lower_list:
        cand_words = set(re.split(r"[^a-z0-9]+", cand_lower))
        overlap_words: list[str] = []
        for w in norm_words:
            if w in cand_words:
                # Exact-word match — always valid.
                overlap_words.append(w)
                continue
            if len(w) >= 5:
                # Allow prefix/suffix variants for longer words only
                # ("respiratory" matches "respiratory_failure" etc.) but
                # forbid mid-word substring matches like "lung"⊂"madelung".
                for cw in cand_words:
                    if cw.startswith(w) or cw.endswith(w):
                        overlap_words.append(w)
                        break
        if not overlap_words:
            continue
        # Ratio = #overlap / #norm_words (favor phrases that cover the input)
        ratio = len(overlap_words) / len(norm_words)
        if ratio < 0.4:
            continue
        if best is None or ratio > best[2]:
            best = (cand_hp, cand_lower, ratio)
        if ratio >= 1.0:
            break
    if best is not None and best[2] >= 0.4:
        hp = best[0]
        return HPOTerm(
            hpo_id=hp, name=idx.id_to_name[hp],
            confidence=min(0.7, best[2]),
            matched_via="substring",
            genes=idx.id_to_genes.get(hp, ()),
        )

    # 4. Sub-phrase fallback — split at common conjunctions / prepositions
    #    and try each chunk. Helps when the upstream router emits a long
    #    compound phrase like "severe abdominal pain after fatty meals".
    if any(sep in norm for sep in (" and ", " after ", " with ", " when ", " while ", " before ")):
        chunks = re.split(r"\s+(?:and|after|with|when|while|before)\s+", norm)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk or chunk == norm:
                continue
            sub = resolve(chunk)
            if sub.hpo_id:
                # Cap confidence — sub-phrase fallback is less certain.
                return HPOTerm(
                    hpo_id=sub.hpo_id, name=sub.name,
                    confidence=min(sub.confidence, 0.85),
                    matched_via=f"sub:{sub.matched_via}",
                    genes=sub.genes,
                )

    # 5. Fallback — unresolved. Per spec, don't fabricate.
    return HPOTerm(
        hpo_id="", name="", confidence=0.0,
        matched_via="fallback", genes=(),
    )


def resolve_many(phrases: list[str]) -> list[HPOTerm]:
    """Resolve each phrase independently; preserves input order."""
    return [resolve(p) for p in phrases]


def genes_for(hpo_id: str) -> tuple[str, ...]:
    """Direct gene lookup by HPO ID."""
    return _index().id_to_genes.get(hpo_id, ())


def term_name(hpo_id: str) -> str | None:
    """Direct name lookup by HPO ID."""
    return _index().id_to_name.get(hpo_id)
