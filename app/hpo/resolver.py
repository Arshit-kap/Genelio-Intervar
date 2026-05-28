"""HPO resolution — symptom phrase → HPO term(s) → ranked gene set.

Backed by two bundled, gzipped TSVs from the official HPO release:
  data/hpo_terms.tsv.gz     : hpo_id → hpo_name    (~12k rows)
  data/hpo_to_genes.tsv.gz  : hpo_id → gene_symbol (~910k rows)

Reuses the same files bundled with genelio_backend. The path is resolved
relative to this file so it works on any deployment layout where both
projects live side-by-side.

Resolution strategies (in order):
  1. Lay-synonym dictionary   — lay English → canonical HPO name
  2. Exact name match         — case-insensitive against HPO names
  3. Substring match          — word-overlap ratio ≥ 0.4
  4. Sub-phrase split         — split on conjunctions, recurse
  5. Fallback → empty term    — caller asks user to clarify (1 question/turn)
"""
from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)

# HPO data files bundled alongside this module in app/hpo/data/.
# Fallback: look in genelio_backend sibling directory (local dev).
_MODULE_DATA = Path(__file__).resolve().parent / "data"
_SIBLING_DATA = Path(__file__).resolve().parent.parent.parent / "genelio_backend" / "chatbot" / "agentic" / "data"
_DATA_DIR = _MODULE_DATA if (_MODULE_DATA / "hpo_terms.tsv.gz").exists() else _SIBLING_DATA
_TERMS_FILE    = _DATA_DIR / "hpo_terms.tsv.gz"
_MAPPINGS_FILE = _DATA_DIR / "hpo_to_genes.tsv.gz"


@dataclass(frozen=True)
class HPOTerm:
    """A resolved HPO match for a single lay-language symptom phrase."""
    hpo_id: str
    name: str
    confidence: float
    matched_via: str        # "synonym" | "exact" | "substring" | "sub:*" | "fallback"
    genes: Tuple[str, ...] = field(default_factory=tuple)

    GENE_DISPLAY_CAP = 10

    def resolved(self) -> bool:
        return bool(self.hpo_id)

    def as_dict(self) -> dict:
        return {
            "hpo_id":      self.hpo_id,
            "name":        self.name,
            "confidence":  self.confidence,
            "matched_via": self.matched_via,
            "gene_count":  len(self.genes),
            "genes_sample": list(self.genes[:self.GENE_DISPLAY_CAP]),
        }


@dataclass
class _Index:
    id_to_name:      Dict[str, str]
    name_to_id:      Dict[str, str]
    id_to_genes:     Dict[str, Tuple[str, ...]]
    name_lower_list: List[Tuple[str, str]]   # (lower_name, hpo_id) sorted longest-first


@lru_cache(maxsize=1)
def _index() -> _Index:
    if not _TERMS_FILE.exists() or not _MAPPINGS_FILE.exists():
        raise FileNotFoundError(
            f"HPO data files not found under {_DATA_DIR}. "
            "Ensure genelio_backend/chatbot/agentic/data/ exists alongside this project."
        )

    log.info("Loading HPO index from %s", _DATA_DIR)

    id_to_name: Dict[str, str] = {}
    with gzip.open(_TERMS_FILE, "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                id_to_name[parts[0]] = parts[1]

    id_to_genes_lists: Dict[str, List[str]] = {}
    with gzip.open(_MAPPINGS_FILE, "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                id_to_genes_lists.setdefault(parts[0], []).append(parts[1])

    id_to_genes: Dict[str, Tuple[str, ...]] = {
        hp: tuple(sorted(set(genes))) for hp, genes in id_to_genes_lists.items()
    }
    name_to_id: Dict[str, str] = {
        name.lower(): hp for hp, name in id_to_name.items()
    }
    name_lower_list = sorted(
        ((name.lower(), hp) for hp, name in id_to_name.items()),
        key=lambda x: -len(x[0]),
    )

    log.info(
        "HPO index ready: %d terms, %d unique genes",
        len(id_to_name),
        len({g for genes in id_to_genes.values() for g in genes}),
    )
    return _Index(
        id_to_name=id_to_name,
        name_to_id=name_to_id,
        id_to_genes=id_to_genes,
        name_lower_list=name_lower_list,
    )


_LAY_SYNONYMS: Dict[str, str] = {
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
    "tired easily": "Fatigue",
    "fatigue": "Fatigue",
    "exhausted": "Fatigue",
    # Developmental
    "developmental delay": "Global developmental delay",
    "slow development": "Global developmental delay",
    # Neuro
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
    # Liver
    "yellow skin": "Jaundice",
    "yellow eyes": "Jaundice",
    # Movement disorders
    "tremor": "Tremor",
    "shaking hands": "Tremor",
    # Organ / body-system (single-word) — map to HPO root terms
    # These prevent bad substring matches like "lung" → "Madelung-like forearm deformities"
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

_SHORT_STOP = {"the", "and", "for", "with", "have", "has", "had",
               "this", "that", "very", "really", "much", "some",
               "any", "all", "lot", "are", "was", "were"}


def _normalize(phrase: str) -> str:
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
    return p.rstrip(".,!?;:")


def resolve(phrase: str) -> HPOTerm:
    """Resolve a single lay-language symptom phrase to an HPO term + genes.

    Returns a sentinel term with hpo_id=="" and confidence==0.0 when no
    match is found. The caller must ask the user to clarify — never fabricate.
    """
    idx = _index()
    norm = _normalize(phrase)

    # 1. Synonym dictionary
    if norm in _LAY_SYNONYMS:
        canonical = _LAY_SYNONYMS[norm]
        hp = idx.name_to_id.get(canonical.lower())
        if hp:
            return HPOTerm(
                hpo_id=hp, name=idx.id_to_name[hp],
                confidence=0.95, matched_via="synonym",
                genes=idx.id_to_genes.get(hp, ()),
            )

    # 2. Exact name match
    hp = idx.name_to_id.get(norm)
    if hp:
        return HPOTerm(
            hpo_id=hp, name=idx.id_to_name[hp],
            confidence=0.90, matched_via="exact",
            genes=idx.id_to_genes.get(hp, ()),
        )

    # 3. Substring match (word-overlap ratio ≥ 0.4)
    norm_words = [w for w in re.split(r"[^a-z0-9]+", norm)
                  if w and w not in _SHORT_STOP and len(w) >= 4]
    if norm_words:
        best: Tuple[str, str, float] | None = None
        for cand_lower, cand_hp in idx.name_lower_list:
            cand_words = set(re.split(r"[^a-z0-9]+", cand_lower))
            # Word-boundary-only matching: require the input word to match a
            # tokenised word of the candidate term exactly, OR (for ≥5-char
            # input words) as a prefix/suffix of a tokenised word.
            # Mid-word substring matches (e.g. "lung" ⊂ "madelung") are NOT
            # accepted — this was the root cause of HPO mis-routing.
            overlap_words: List[str] = []
            for w in norm_words:
                if w in cand_words:
                    # Exact word match — always valid
                    overlap_words.append(w)
                    continue
                if len(w) >= 5:
                    # Prefix/suffix only for longer words
                    for cw in cand_words:
                        if cw.startswith(w) or cw.endswith(w):
                            overlap_words.append(w)
                            break
            if not overlap_words:
                continue
            ratio = len(overlap_words) / len(norm_words)
            if ratio < 0.4:
                continue
            if best is None or ratio > best[2]:
                best = (cand_hp, cand_lower, ratio)
            if ratio >= 1.0:
                break
        if best and best[2] >= 0.4:
            hp = best[0]
            return HPOTerm(
                hpo_id=hp, name=idx.id_to_name[hp],
                confidence=min(0.7, best[2]),
                matched_via="substring",
                genes=idx.id_to_genes.get(hp, ()),
            )

    # 4. Sub-phrase split on conjunctions
    if any(sep in norm for sep in (" and ", " after ", " with ", " when ", " while ", " before ")):
        chunks = re.split(r"\s+(?:and|after|with|when|while|before)\s+", norm)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk or chunk == norm:
                continue
            sub = resolve(chunk)
            if sub.hpo_id:
                return HPOTerm(
                    hpo_id=sub.hpo_id, name=sub.name,
                    confidence=min(sub.confidence, 0.85),
                    matched_via=f"sub:{sub.matched_via}",
                    genes=sub.genes,
                )

    # 5. Fallback — unresolved
    return HPOTerm(hpo_id="", name="", confidence=0.0, matched_via="fallback", genes=())


def resolve_many(phrases: List[str]) -> List[HPOTerm]:
    """Resolve each phrase independently; preserves input order."""
    return [resolve(p) for p in phrases]


def rank_and_cap_genes(resolved_terms: List[HPOTerm], cap: int = 300) -> List[str]:
    """Return up to `cap` genes ranked by HPO specificity.

    Specificity score for a gene = sum of (1 / gene_count_for_term) across
    all HPO terms the gene appears in. Genes in more specific (fewer-gene)
    terms score higher and are returned first.
    """
    gene_scores: Dict[str, float] = {}
    for term in resolved_terms:
        if not term.hpo_id or not term.genes:
            continue
        specificity = 1.0 / len(term.genes)
        for gene in term.genes:
            gene_scores[gene] = gene_scores.get(gene, 0.0) + specificity

    ranked = sorted(gene_scores.items(), key=lambda x: -x[1])
    return [gene for gene, _ in ranked[:cap]]
