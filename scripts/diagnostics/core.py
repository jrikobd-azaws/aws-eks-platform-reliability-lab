"""D1 offline-only incident and evidence core. No subprocesses or cloud clients."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
MAX_BYTES = 1_000_000
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SECRET = re.compile(r"(?i)((?:password|secret|token|api[_-]?key|authorization|private[_-]?key)\s*[:=]\s*)([^\s,;]+)")
SIGNATURES = (
    ("PG_SCHEMA_MISSING", r"UndefinedTable|relation .+ does not exist|alembic.*(?:missing|revision)", "postgresql",
     "Check migration version and schema inventory without modifying the database."),
    ("PG_TLS", r"certificate verify failed|unknown ca|sslmode|TLS handshake", "postgresql",
     "Check trust chain, server name and client TLS settings read-only."),
    ("ARGO_SOURCE", r"source repo.*not permitted|repository.*not permitted|AppProject.*denied", "gitops",
     "Inspect the AppProject sourceRepos and effective Application source."),
    ("PROM_CRD_LIMIT", r"annotations.*262144|metadata.annotations.*too long", "observability",
     "Inspect CRD size and server-side apply ownership offline."),
    ("PROM_OPERATOR_TLS", r"observability-admission|admission.*secret.*not found", "observability",
     "Compare rendered operator TLS configuration with webhook enablement."),
    ("K8S_PSA", r"violates PodSecurity|violates Pod Security|baseline.*privileged", "kubernetes",
     "Inspect Pod Security policy and workload securityContext."),
    ("K8S_OOM", r"OOMKilled|exit code 137|reason.*OOM", "kubernetes",
     "Compare container memory limit, observed usage and node headroom."),
    ("ALERT_RECEIVER", r"undefined receiver|receiver.*null", "observability",
     "Inspect effective Alertmanager route and declared receivers."),
    ("MONITOR_SELECTOR", r"ServiceMonitor.*selector|target.*not discovered|metrics.*absent", "observability",
     "Compare Service labels, ServiceMonitor selectors and Prometheus selectors."),
    ("GITOPS_OWNERSHIP", r"shared resource|multiple applications|resource.*not permitted|outofsync", "gitops",
     "Inspect ownership, drift and pruning policy without syncing."),
    ("TF_STATE", r"state lineage|serial mismatch|state lock|backend.*denied", "terraform",
     "Compare plan, backend, workspace and state metadata offline."),
    ("RECOVERY_PROVENANCE", r"source revision mismatch|evidence.*root|backup checksum|shutdown readiness", "lifecycle",
     "Compare source revision, manifests, checksums and certification metadata."),
)
ALLOWED = {"new", "investigating", "verification", "closed", "reopened"}
SENSITIVE_KEYS = {"secret", "password", "token", "authorization", "private_key", "api_key"}

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, str):
        return SECRET.sub(r"\1[REDACTED]", value)
    return value

def safe_root(path):
    root = Path(path).expanduser().absolute()
    if root.is_symlink():
        raise ValueError("symlink evidence root refused")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_mode & 0o077:
        raise ValueError("evidence root must be private (0700)")
    return root.resolve()

def safe_file(root, name):
    if not IDENTIFIER.fullmatch(name):
        raise ValueError("invalid incident identifier")
    path = root / (name + ".json")
    if path.is_symlink() or path.parent.resolve() != root:
        raise ValueError("symlink or path traversal refused")
    return path

def load(root, name):
    path = safe_file(root, name)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("missing or nonprivate incident")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("incident exceeds size limit")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or data.get("id") != name:
        raise ValueError("unexpected incident schema or identity")
    return data

def save(root, record):
    path = safe_file(root, record["id"])
    payload = json.dumps(redact(record), indent=2, ensure_ascii=False, sort_keys=True)
    if len(payload.encode()) > MAX_BYTES:
        raise ValueError("incident exceeds size limit")
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload + "\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path

def update(root, record):
    path = safe_file(root, record["id"])
    current = load(root, record["id"])
    if current["id"] != record["id"]:
        raise ValueError("identity mismatch")
    payload = json.dumps(redact(record), indent=2, sort_keys=True)
    if len(payload.encode()) > MAX_BYTES:
        raise ValueError("incident exceeds size limit")
    import os
    temp = root / (record["id"] + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload + "\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def create(root, name, symptom, impact, component):
    if not symptom.strip():
        raise ValueError("symptom is required")
    record = dict(schema=SCHEMA, id=name, created_utc=utcnow(), updated_utc=utcnow(),
                  state="new", symptoms=redact(symptom), impact=redact(impact),
                  component=component, evidence=[], observations=[], hypotheses=[],
                  missing_evidence=[], next_tests=[], signature_matches=[])
    save(root, record)
    return record

def add_evidence(root, record, filename):
    evidence = Path(filename).expanduser()
    if evidence.is_symlink() or not evidence.is_file():
        raise ValueError("missing or symlinked evidence")
    if evidence.stat().st_size > MAX_BYTES:
        raise ValueError("evidence exceeds maximum size")
    blob = evidence.read_bytes()
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("only UTF-8 offline evidence is accepted") from exc
    record["evidence"].append(dict(source=str(evidence.resolve()), sha256=hashlib.sha256(blob).hexdigest(),
                                  collected_utc=utcnow(), text=redact(text)))
    record["updated_utc"] = utcnow()
    update(root, record)
    return record

def diagnose(record):
    fragments = [str(record.get("symptoms", ""))]
    fragments += [str(e.get("text", "")) for e in record.get("evidence", [])]
    text = "\n".join(fragments)
    hits = []
    for ident, pattern, domain, test in SIGNATURES:
        if re.search(pattern, text, re.IGNORECASE):
            hits.append(dict(id=ident, domain=domain, confidence="candidate",
                             evidence_based=True, next_test=test))
    record["signature_matches"] = hits
    record["observations"] = [dict(type="pattern_match", signature=h["id"]) for h in hits]
    record["hypotheses"] = [dict(hypothesis=h["id"], status="unverified") for h in hits]
    record["missing_evidence"] = ([] if hits else ["No recognised failure signature; gather events, logs and configuration."])
    record["next_tests"] = [h["next_test"] for h in hits] or [
        "Collect read-only component events, deployment status and recent log excerpts."]
    return record

def markdown(record):
    r = redact(record)
    lines = [f"# Incident {r['id']}", "", f"State: {r['state']}", "",
             f"Symptoms: {r['symptoms']}", f"Impact: {r['impact']}", "",
             "## Candidate diagnoses (not verified root causes)"]
    lines += [f"- {x['id']} ({x['domain']})" for x in r["signature_matches"]] or ["- None; insufficient evidence."]
    lines += ["", "## Next read-only tests"]
    lines += [f"- {x}" for x in r["next_tests"]]
    return "\n".join(lines) + "\n"
