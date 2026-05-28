import sys, json
d = json.load(sys.stdin)
print("intent:", d.get("type","?"))
print("rows:", d.get("row_count", 0))
ans = d.get("response","") or d.get("answer","")
print("answer:", ans[:400])
