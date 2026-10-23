#!/usr/bin/env python3
"""Offline incident command line; no AWS/Kubernetes/network command execution."""
import argparse
import json
import sys
from pathlib import Path
from core import safe_root, create, load, update, add_evidence, diagnose, markdown, redact, ALLOWED, utcnow
from domain_rules import scenario_rules

def main(argv=None):
    p = argparse.ArgumentParser(prog="labdiag", description="Offline evidence-grounded incident diagnostics")
    p.add_argument("--store", default="~/.local/share/azawslab/diagnostics", help="private incident record directory")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create"); c.add_argument("id"); c.add_argument("--symptom", required=True); c.add_argument("--impact", default="unknown"); c.add_argument("--component", default="unknown")
    s = sub.add_parser("show"); s.add_argument("id")
    a = sub.add_parser("evidence"); a.add_argument("id"); a.add_argument("file")
    i = sub.add_parser("investigate"); i.add_argument("id")
    u = sub.add_parser("update"); u.add_argument("id"); u.add_argument("--state", choices=sorted(ALLOWED), required=True)
    f = sub.add_parser("report"); f.add_argument("id"); f.add_argument("--format", choices=("json", "markdown"), default="markdown")
    r = sub.add_parser("replay"); r.add_argument("fixture", help="offline JSON fixture with id, symptoms, evidence strings")
    cov = sub.add_parser("coverage"); cov.add_argument("--index", default=None, help="authoritative 43-scenario markdown index")
    inv = sub.add_parser("inventory"); inv.add_argument("--registry", default=None, help="versioned historical incident registry")
    args = p.parse_args(argv)
    if args.command == "inventory":
        registry = Path(args.registry) if args.registry else Path(__file__).resolve().parents[2] / "scenarios/D1_HISTORICAL_INVENTORY.json"
        if registry.is_symlink() or not registry.is_file() or registry.stat().st_size > 1_000_000:
            raise ValueError("invalid offline historical registry")
        data = json.loads(registry.read_text(encoding="utf-8"))
        incidents = data.get("incidents")
        if data.get("schema") != 1 or not isinstance(incidents, list):
            raise ValueError("invalid historical inventory schema")
        if len({x["id"] for x in incidents}) != len(incidents):
            raise ValueError("duplicate historical ID")
        print(json.dumps(redact({"schema": 1, "count": len(incidents), "incidents": incidents}), indent=2))
        return 0
    if args.command == "coverage":
        index = Path(args.index) if args.index else Path(__file__).resolve().parents[2] / "scenarios/SCENARIO_INDEX.md"
        import re
        raw = index.read_text(encoding="utf-8")
        catalogue = raw.partition("## Catalogue")[2].partition("## Failure-injection")[0]
        rows = re.findall(r"^\|\s*((?:K8S|EKS|TF|HELM|ARGO|INC)-\d+)\s*\|\s*([^|]+)\|\s*(\d)\s*\|\s*(full|compact)\s*\|\s*(planned|in-progress|done)\s*\|", catalogue, re.M)
        if len(rows) != 43 or len({r[0] for r in rows}) != 43:
            raise ValueError("scenario index must contain exactly 43 unique entries")
        rule_map = scenario_rules()
        print(json.dumps({"schema": 1, "scenario_count": len(rows), "coverage": [
            {"id": ident, "name": name.strip(), "wave": int(wave), "scenario_status": status,
             "registered": True, "collector_available": False, "detection_rule_available": ident in rule_map,
             "rules": rule_map.get(ident, []), "offline_regression_passed": False, "live_validated": False, "documentation_published": False}
            for ident, name, wave, _, status in rows]}, indent=2))
        return 0
    if args.command == "replay":
        path = Path(args.fixture)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
            raise ValueError("invalid fixture")
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj.get("evidence", []), list) or any(not isinstance(x, (str, dict)) for x in obj.get("evidence", [])):
            raise ValueError("fixture evidence must consist of strings or objects")
        if not isinstance(obj.get("evidence", []), list):
            raise ValueError("fixture evidence must be a list")
        record = {"schema": 1, "id": obj["id"], "state": "investigating",
                  "symptoms": str(obj["symptoms"]), "impact": str(obj.get("impact", "unknown")),
                  "evidence": [{"text": json.dumps(x, sort_keys=True) if isinstance(x, dict) else x} for x in obj["evidence"]]}
        result = redact(diagnose(record))
        expected = obj.get("expected_signatures")
        if expected is not None:
            if not isinstance(expected, list) or any(not isinstance(x, str) for x in expected):
                raise ValueError("expected_signatures must be a list of strings")
            detected = {h["id"] for h in result["signature_matches"]}
            missing = set(expected) - detected
            if missing:
                raise ValueError("replay expectation failed: " + ", ".join(sorted(missing)))
        print(json.dumps(result, indent=2))
        return 0
    root = safe_root(args.store)
    if args.command == "create":
        obj = create(root, args.id, args.symptom, args.impact, args.component)
    else:
        obj = load(root, args.id)
        if args.command == "evidence":
            obj = add_evidence(root, obj, args.file)
        elif args.command == "investigate":
            obj = diagnose(obj); obj["state"] = "investigating"; obj["updated_utc"] = utcnow(); update(root, obj)
        elif args.command == "update":
            obj["state"] = args.state; obj["updated_utc"] = utcnow(); update(root, obj)
    if args.command == "report" and args.format == "markdown":
        print(markdown(obj), end="")
    else:
        print(json.dumps(redact(obj), indent=2))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
