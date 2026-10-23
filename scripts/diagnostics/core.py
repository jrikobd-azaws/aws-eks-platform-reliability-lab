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
SECRET = re.compile(r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|authorization|client_secret|private[_-]?key)\s*[:=]\s*[\"']?)([^\s,\"';]+)")
BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9_.~+/-]+={0,2}")
URI_SECRET = re.compile(r"(?i)(\b(?:postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://[^:/@\s]+:)[^@\s/]+(@)")
QUERY_SECRET = re.compile(r"(?i)([?&](?:password|token|secret|api_key)=)[^&#\s]+")
AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
PEM = re.compile(r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----")
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
        return {k: ("[REDACTED]" if any(x in k.lower().replace("-", "_") for x in SENSITIVE_KEYS) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, str):
        value = PEM.sub("[REDACTED PRIVATE KEY]", value)
        value = URI_SECRET.sub(r"\1[REDACTED]\2", value)
        value = BEARER.sub(r"\1[REDACTED]", value)
        value = QUERY_SECRET.sub(r"\1[REDACTED]", value)
        value = SECRET.sub(r"\1[REDACTED]", value)
        return AWS_KEY.sub("[REDACTED AWS KEY]", value)
    return value

def safe_root(path):
    root = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("symlink in evidence root ancestry refused")
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
    import secrets
    temp = root / (record["id"] + "-" + secrets.token_hex(8) + ".tmp")
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
    evidence = Path(filename).expanduser().absolute()
    if any(p.is_symlink() for p in (evidence, *evidence.parents)) or not evidence.is_file():
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


def verify_evidence(record):
    """Re-hash original local evidence, failing closed on changed or missing files."""
    checks = []
    for item in record.get("evidence", []):
        path = Path(str(item.get("source", "")))
        status = "unverifiable"
        try:
            if (path.is_absolute() and path.is_file() and
                not any(p.is_symlink() for p in (path, *path.parents)) and
                path.stat().st_size <= MAX_BYTES):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                status = "verified" if digest.hexdigest() == item.get("sha256") else "changed"
        except (OSError, ValueError):
            status = "unverifiable"
        checks.append({"source": redact(str(path)), "status": status})
    return checks


def transition(record, requested, note=None):
    if requested not in ALLOWED:
        raise ValueError("invalid incident state")
    if requested == "closed":
        if note is not None:
            if not note.strip():
                raise ValueError("administrative closure needs a documented reason")
            record["closure"] = {"kind": "administrative_unresolved", "note": redact(note)}
            record["resolution_verified"] = False
        else:
            checks = verify_evidence(record)
            if (record.get("state") != "verification" or not checks or
                any(x["status"] != "verified" for x in checks) or
                not record.get("verification_note")):
                raise ValueError("resolved closure requires verification state, intact evidence and review note")
            record["closure"] = {"kind": "resolved", "note": redact(record["verification_note"])}
            record["resolution_verified"] = True
    if requested == "reopened":
        record.pop("closure", None)
        record["resolution_verified"] = False
    record["state"] = requested
    record["updated_utc"] = utcnow()
    return record

def diagnose(record, *, offline_fixture=False):
    fragments = [str(record.get("symptoms", ""))]
    fragments += [str(e.get("text", "")) for e in record.get("evidence", [])]
    evidence_checks = ([{"status": "synthetic_unverified"} for _ in record.get("evidence", [])]
                       if offline_fixture else verify_evidence(record))
    record["evidence_verification"] = evidence_checks
    record["synthetic_replay"] = bool(offline_fixture)
    fragments = [str(record.get("symptoms", ""))]
    fragments += [str(item.get("text", "")) for item, result in zip(record.get("evidence", []), evidence_checks)
                  if result["status"] == "verified" or offline_fixture]
    text = "\n".join(fragments)
    hits = []
    for ident, pattern, domain, test in SIGNATURES:
        if re.search(pattern, text, re.IGNORECASE):
            hits.append(dict(id=ident, domain=domain, confidence="candidate",
                             evidence_based=True, next_test=test))
    from domain_rules import text_matches
    hits.extend(x for x in text_matches(text) if x["id"] not in {h["id"] for h in hits})
    from collectors import collect_saved_json
    structured = []
    for evidence, checked in zip(record.get("evidence", []), evidence_checks):
        if checked["status"] == "verified" or offline_fixture:
            structured += collect_saved_json(evidence.get("text", ""))
    structured += collect_saved_json(str(record.get("symptoms", "")))
    record["signature_matches"] = hits
    record["observations"] = [dict(type="pattern_match", signature=h["id"]) for h in hits] + structured[:100]
    record["hypotheses"] = [dict(hypothesis=h["id"], status="unverified",
                                   supporting_observations=[o["type"] for o in structured
                                     if (o.get("type", "").startswith(h["domain"])
                                         or (h["domain"] == "kubernetes" and o.get("type", "").startswith("kubernetes")))][:10])
                            for h in hits]
    ids = {h["id"] for h in hits}
    correlations = []
    if "K8S_IMAGE_PULL" in ids and "ARGO_SYNC" in ids:
        correlations.append(dict(type="cross_layer", components=["gitops", "kubernetes"],
                                 hypothesis="GitOps degradation may be related to image retrieval failure",
                                 confidence="unverified"))
    if "PG_CONNECT" in ids and ("K8S_PROBE" in ids or "K8S_SCHEDULING" in ids):
        correlations.append(dict(type="cross_layer", components=["postgresql", "kubernetes"],
                                 hypothesis="Application readiness may depend on database connectivity",
                                 confidence="unverified"))
    record["correlations"] = correlations
    record["missing_evidence"] = [
        "Candidate signatures do not establish a root cause; independently verify the affected component, timeline and configuration."
    ] if hits else ["No recognised failure signature; gather events, logs and configuration."]
    if any(x["status"] != "verified" for x in evidence_checks):
        record["missing_evidence"].append("Original saved evidence changed or missing; independent verification failed.")
    record["next_tests"] = list(dict.fromkeys(h["next_test"] for h in hits)) or [
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
