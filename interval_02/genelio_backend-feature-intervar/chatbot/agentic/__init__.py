"""Agentic layer for the clinical_csv chat path.

Implements the HPO-aware Q&A flow described in
``intervar-hpo-integration-flow.pdf``: a router-LLM front end that
classifies the user's intent, an HPO Resolution layer that maps lay
symptoms to phenotype IDs and gene sets, a deterministic variant filter
+ pathogenicity-ranking stage, and a final answer-generator LLM call
constrained to the filtered context.

Public entry point: ``orchestrator.handle_clinical_csv_message``.
"""
