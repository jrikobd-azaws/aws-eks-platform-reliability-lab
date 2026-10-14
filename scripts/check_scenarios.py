#!/usr/bin/env python3
"""Validate scenarios/SCENARIO_INDEX.md. Exit 1 on any problem."""
import re, sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
t = (root / "scenarios" / "SCENARIO_INDEX.md").read_text()
errs = []
if re.search(r"optional", t, re.I):
    errs.append("the word 'optional' must not appear in the scenario index")
row = re.compile(r"^\|\s*((?:K8S|EKS|TF|HELM|ARGO|INC)-\d+)\s*\|(.*)\|\s*$")
cat, det, section = {}, {}, None
for line in t.splitlines():
    if line.startswith("## Catalogue"): section = "cat"
    elif line.startswith("## Failure-injection"): section = "det"
    m = row.match(line)
    if not m: continue
    cells = [c.strip() for c in m.group(2).split("|")]
    (cat if section == "cat" else det)[m.group(1)] = cells
if len(cat) != 43: errs.append(f"catalogue has {len(cat)} entries, expected 43")
if set(cat) != set(det): errs.append("catalogue and detail tables differ: " + str(sorted(set(cat) ^ set(det))))
for i, c in cat.items():
    if len(c) != 5: errs.append(f"{i}: expected 6 columns"); continue
    name, wave, depth, status, ev = c
    if wave not in {"1","2","3","4","5","6"}: errs.append(f"{i}: bad wave {wave}")
    if depth not in {"full","compact"}: errs.append(f"{i}: bad depth {depth}")
    if status not in {"planned","in-progress","done"}: errs.append(f"{i}: bad status {status}")
    if status == "done":
        if ev in {"", "-"}: errs.append(f"{i}: done requires an evidence path")
        elif not (root / ev).exists(): errs.append(f"{i}: evidence path does not exist: {ev}")
for i, d in det.items():
    if len(d) != 5 or any(not x for x in d): errs.append(f"{i}: detail row incomplete")
done = sum(1 for c in cat.values() if len(c) == 5 and c[3] == "done")
print(f"scenarios={len(cat)} done={done}")
if errs:
    print("\n".join("ERROR: " + e for e in errs)); sys.exit(1)
print("scenario index OK")
