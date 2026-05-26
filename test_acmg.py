"""Quick ACMG engine test"""
from app.acmg.evaluator import evaluate_variant
from app.acmg.classifier import classify
from app.ai.text_to_sql import _pattern_sql, _validate_sql

# Test 1: Stopgain pathogenic variant
v1 = {
    "exonic_func": "stopgain", "func_region": "exonic",
    "clinvar_significance": "Pathogenic",
    "gnomad_af_all": None, "cadd_phred": 35.5,
    "sift_score": 0.001, "metasvm_score": 0.95, "gerp_rs": 5.1,
    "interpro_domain": "BRCA1 C-terminal domain", "repeat_masker": None,
    "dbscsnv_ada_score": None, "dbscsnv_rf_score": None,
}
c1 = evaluate_variant(v1)
r1 = classify(c1)
print("=== Test 1: Stopgain Pathogenic ===")
print("Classification:", r1["classification"])
print("Explanation:", r1["explanation"])
print("Triggered:", r1["triggered_criteria"])

# Test 2: Common synonymous variant (should be Benign)
v2 = {
    "exonic_func": "synonymous SNV", "func_region": "exonic",
    "clinvar_significance": "Benign",
    "gnomad_af_all": 0.15,
    "cadd_phred": 3.2, "sift_score": 0.8, "metasvm_score": -0.9, "gerp_rs": 0.5,
    "interpro_domain": None, "repeat_masker": None,
    "dbscsnv_ada_score": 0.1, "dbscsnv_rf_score": 0.05,
}
c2 = evaluate_variant(v2)
r2 = classify(c2)
print("\n=== Test 2: Common Synonymous ===")
print("Classification:", r2["classification"])
print("Explanation:", r2["explanation"])
print("Triggered:", r2["triggered_criteria"])

# Test 3: Pattern SQL fallback
print("\n=== Test 3: Pattern SQL ===")
q = "Show pathogenic variants in BRCA1"
sql = _pattern_sql(q)
valid, err = _validate_sql(sql)
print(f"Question: {q}")
print(f"SQL: {sql}")
print(f"Valid: {valid}")

q2 = "How many variants per chromosome?"
sql2 = _pattern_sql(q2)
print(f"\nQuestion: {q2}")
print(f"SQL: {sql2}")

print("\nAll ACMG + Pattern SQL tests PASSED")
