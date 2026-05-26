"""HPO resolution module for the Genomic Q&A Engine."""
from .resolver import resolve, resolve_many, rank_and_cap_genes, HPOTerm

__all__ = ["resolve", "resolve_many", "rank_and_cap_genes", "HPOTerm"]
