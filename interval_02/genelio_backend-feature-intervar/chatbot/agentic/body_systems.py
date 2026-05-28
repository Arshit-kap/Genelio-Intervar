"""Body-system / organ-based query router.

Spec §C1: a vague organ-based question like *"Do I have any variants
related to lung disease?"* should map a lay term ("lungs") to a broad
HPO category (HP:0002086 ``Abnormality of the respiratory system``),
gather its descendant terms, take the **union of their gene sets**, then
intersect that union with the patient's variants.

The official descendant traversal requires the HPO is_a edges from the
HPO OBO file, which we don't bundle today (the slim TSV the agentic
layer ships only encodes term-name and term→gene pairs). Until the
OBO loader lands, we approximate descendant traversal with a
**lexical-overlap fan-out**: starting from a curated lay→HP root, we
also union in the gene sets of every HPO term whose *name* contains
the body-system keyword (e.g. anything mentioning "respiratory",
"pulmonary", "lung", "bronch", "trachea"). This is conservative-enough
for the patient-facing demo (false-positives are mostly other terms in
the same anatomic neighbourhood) and clearly less correct than real
ontology traversal. The hook is here for a real ``descendants_of(hp_id)``
implementation when the OBO is loaded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from . import hpo


# ---------------------------------------------------------------------------
# Curated lay-term → (HP root, lexical-keyword fan-out)
# ---------------------------------------------------------------------------
#
# Each entry is a body-system / organ keyword the patient might say in
# plain language. We tie it to:
#   * ``root``  — the canonical broad HP term we want to anchor on.
#   * ``keys``  — extra lowercased substrings to lexically fan out across
#                 the HPO term-name table when we lack OBO descendants.
#
# Sourced from the HPO top-level "Phenotypic abnormality" categories:
#   HP:0001626  Abnormality of the cardiovascular system   (heart)
#   HP:0002086  Abnormality of the respiratory system      (lungs)
#   HP:0000119  Abnormality of the genitourinary system    (kidney/urinary)
#   HP:0012443  Abnormality of brain morphology            (brain — approx)
#   HP:0001939  Abnormality of metabolism/homeostasis
#   HP:0001392  Abnormality of the liver                   (liver)
#   HP:0000478  Abnormality of the eye                     (eyes)
#   HP:0000598  Abnormality of the ear                     (ears)
#   HP:0025031  Abnormality of the digestive system        (gut)
#   HP:0003011  Abnormality of the musculature             (muscles)
#   HP:0000951  Abnormality of the skin                    (skin)
#   HP:0000924  Abnormality of the skeletal system         (bones)
#   HP:0001871  Abnormality of blood and blood-forming tissues (blood)
#   HP:0000818  Abnormality of the endocrine system        (endocrine)
#   HP:0002664  Neoplasm                                   (cancer)
#
# When we don't have these specific HP IDs in the bundled index we still
# get a useful answer from the keyword fan-out.

@dataclass(frozen=True)
class BodySystem:
    name: str
    root_hpo_id: str
    keys: tuple[str, ...]
    lay_terms: tuple[str, ...] = field(default_factory=tuple)


BODY_SYSTEMS: tuple[BodySystem, ...] = (
    BodySystem(
        name="Respiratory system",
        root_hpo_id="HP:0002086",
        keys=("respiratory", "pulmonary", "lung", "bronch", "trachea", "airway"),
        lay_terms=("lung", "lungs", "breathing", "respiratory", "chest", "airway"),
    ),
    BodySystem(
        name="Cardiovascular system",
        root_hpo_id="HP:0001626",
        keys=("cardiac", "heart", "myocard", "aort", "ventric", "atria", "coronary", "valve"),
        lay_terms=("heart", "cardiac", "chest pain", "heart attack", "cardiovascular"),
    ),
    BodySystem(
        name="Genitourinary / kidney system",
        root_hpo_id="HP:0000119",
        keys=("renal", "kidney", "nephro", "bladder", "urinary", "ureter"),
        lay_terms=("kidney", "kidneys", "renal", "urinary", "bladder"),
    ),
    BodySystem(
        name="Brain / nervous system",
        root_hpo_id="HP:0012443",
        keys=("brain", "cerebr", "neuron", "neural", "cortex", "encephal", "cogniti"),
        lay_terms=("brain", "cognitive", "neurological", "memory", "mind"),
    ),
    BodySystem(
        name="Liver / hepatic system",
        root_hpo_id="HP:0001392",
        keys=("hepatic", "liver", "biliary", "bile"),
        lay_terms=("liver", "hepatic", "bile", "jaundice"),
    ),
    BodySystem(
        name="Eye / visual system",
        root_hpo_id="HP:0000478",
        keys=("eye", "ocular", "retin", "cornea", "lens", "vis", "blind"),
        lay_terms=("eye", "eyes", "vision", "sight", "blindness", "blind"),
    ),
    BodySystem(
        name="Ear / auditory system",
        root_hpo_id="HP:0000598",
        keys=("ear", "auditory", "hear", "deaf", "cochlea", "vestibul"),
        lay_terms=("ear", "ears", "hearing", "deafness", "deaf"),
    ),
    BodySystem(
        name="Digestive / GI system",
        root_hpo_id="HP:0025031",
        keys=("gastro", "intestin", "stomach", "bowel", "colon", "duoden",
              "digest", "pancrea"),
        lay_terms=("gut", "stomach", "bowel", "digestive", "intestine", "gi"),
    ),
    BodySystem(
        name="Musculature",
        root_hpo_id="HP:0003011",
        keys=("muscle", "muscular", "myop", "myoton", "myalg"),
        lay_terms=("muscle", "muscles", "muscular"),
    ),
    BodySystem(
        name="Skin",
        root_hpo_id="HP:0000951",
        keys=("skin", "cutaneous", "dermat", "epiderm", "pigment"),
        lay_terms=("skin", "rash", "pigment", "dermal"),
    ),
    BodySystem(
        name="Skeletal system",
        root_hpo_id="HP:0000924",
        keys=("bone", "skelet", "osteo", "vertebr", "spine", "joint"),
        lay_terms=("bone", "bones", "skeleton", "spine", "joints"),
    ),
    BodySystem(
        name="Blood / haematology",
        root_hpo_id="HP:0001871",
        keys=("blood", "haema", "hema", "anemi", "thromb", "platelet", "leuko",
              "lympho", "neutro"),
        lay_terms=("blood", "anemia", "bleeding", "clot"),
    ),
    BodySystem(
        name="Endocrine system",
        root_hpo_id="HP:0000818",
        keys=("endocrine", "thyroid", "adrenal", "pituitary", "diabetes",
              "insulin", "hormone"),
        lay_terms=("endocrine", "hormone", "hormones", "thyroid", "diabetes"),
    ),
    BodySystem(
        name="Neoplasms / cancer",
        root_hpo_id="HP:0002664",
        keys=("neoplas", "tumor", "tumour", "cancer", "carcinom", "leuk",
              "lymphoma", "melanoma", "sarcoma"),
        lay_terms=("cancer", "tumor", "tumour", "neoplasm", "malignancy"),
    ),
)


@dataclass(frozen=True)
class BodySystemMatch:
    body_system: BodySystem
    hpo_ids: tuple[str, ...]   # HP terms (root + lexically-related)
    genes: tuple[str, ...]     # union of their gene sets


def _matches_lay_term(message: str) -> BodySystem | None:
    """Find the body system the user named in plain language."""
    low = (message or "").lower()
    # Prefer the longest match — "kidney" before "ki".
    best: tuple[BodySystem, int] | None = None
    for system in BODY_SYSTEMS:
        for token in system.lay_terms:
            if f" {token} " in f" {low} " or low.startswith(token) or low.endswith(token):
                score = len(token)
                if best is None or score > best[1]:
                    best = (system, score)
    return best[0] if best else None


@lru_cache(maxsize=64)
def _fan_out(system: BodySystem) -> BodySystemMatch:
    """Resolve a body system to all HP terms + the union of their genes.

    Approximate descendant traversal — see module docstring. Cached
    because the lexical scan of the entire HPO term table is non-trivial.
    """
    idx = hpo._index()  # internal, but stable shape — local module
    hp_ids: set[str] = set()
    if system.root_hpo_id in idx.id_to_name:
        hp_ids.add(system.root_hpo_id)
    # Lexical fan-out — every HPO term whose name contains any key.
    for name_lower, hp_id in idx.name_lower_list:
        for k in system.keys:
            if k in name_lower:
                hp_ids.add(hp_id)
                break
    # Union genes across all matched HP terms.
    genes: set[str] = set()
    for hp_id in hp_ids:
        genes.update(idx.id_to_genes.get(hp_id, ()))
    return BodySystemMatch(
        body_system=system,
        hpo_ids=tuple(sorted(hp_ids)),
        genes=tuple(sorted(genes)),
    )


def resolve_body_system(message_or_term: str) -> BodySystemMatch | None:
    """Resolve a body-system lay phrase to (HP terms, gene set).

    Returns ``None`` when the message doesn't reference a known body
    system. Caller (orchestrator) then routes via the regular HPO
    symptom path instead.
    """
    sys = _matches_lay_term(message_or_term)
    if sys is None:
        return None
    return _fan_out(sys)
