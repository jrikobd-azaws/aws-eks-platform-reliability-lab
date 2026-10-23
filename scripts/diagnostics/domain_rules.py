"""Deterministic offline signatures over saved, untrusted evidence.

Each rule yields a candidate, NEVER an established cause. No command execution.
"""
from __future__ import annotations
import re

RULES = (
    ("K8S_IMAGE_PULL", "kubernetes", r"ImagePullBackOff|ErrImagePull|FailedToRetrieveImagePullSecret", "Inspect pod events, image reference, ECR tag and pull permissions.", ("K8S-02", "INC-01")),
    ("K8S_SCHEDULING", "kubernetes", r"FailedScheduling|Insufficient cpu|Insufficient memory|untolerated taint|didn't match Pod", "Compare pod requests, node allocatable, taints and selectors.", ("K8S-03", "INC-05")),
    ("K8S_PROBE", "kubernetes", r"Readiness probe failed|Liveness probe failed|Startup probe failed|Unhealthy.*probe", "Compare rendered probe path, port and timeout with application endpoints.", ("K8S-05",)),
    ("K8S_DNS", "kubernetes", r"NXDOMAIN|SERVFAIL|no such host|Temporary failure in name resolution", "Check requested hostname, namespace and saved CoreDNS logs.", ("K8S-07",)),
    ("K8S_STORAGE", "kubernetes", r"FailedMount|FailedAttachVolume|unbound immediate PersistentVolumeClaims|storageclass.*not found", "Check PVC events, access mode, StorageClass and CSI controller evidence.", ("K8S-09", "EKS-03")),
    ("K8S_RBAC", "kubernetes", r"forbidden.*(?:serviceaccount|pods|secrets|configmaps)|cannot (?:get|list|watch|create).*resource", "Inspect effective RoleBindings and ServiceAccount identity.", ("K8S-10",)),
    ("K8S_NODE", "kubernetes", r"NodeNotReady|KubeletNotReady|node.*NotReady", "Compare node condition changes, kubelet and network evidence.", ("K8S-11",)),
    ("K8S_QUOTA", "kubernetes", r"exceeded quota|ResourceQuota.*exceeded", "Inspect quota usage against namespace requests.", ("K8S-12",)),
    ("EKS_CNI", "eks", r"failed to assign an IP address|FailedCreatePodSandBox|ipamd.*no available", "Compare available subnet IPs, ENIs and aws-node evidence.", ("EKS-02",)),
    ("EKS_IAM", "eks", r"AssumeRoleWithWebIdentity|AccessDenied.*(?:sts|secretsmanager|ecr)|InvalidIdentityToken", "Inspect redacted trust conditions, association and service-account identity.", ("EKS-01", "TF-03")),
    ("ARGO_SYNC", "gitops", r"ComparisonError|SyncFailed|OutOfSync|permission denied.*repository", "Compare source revision, AppProject restrictions and resource owners.", ("ARGO-01", "ARGO-03", "ARGO-04")),
    ("HELM_RELEASE", "helm", r"pending-upgrade|UPGRADE FAILED|cannot patch.*immutable|template:.*error", "Inspect saved helm history, chart templates and rendered values.", ("HELM-02", "HELM-03", "HELM-04")),
    ("TF_LOCK", "terraform", r"Error acquiring the state lock|ConditionalCheckFailedException.*lock", "Identify current lock holder; never force-unlock automatically.", ("TF-01",)),
    ("TF_BACKEND", "terraform", r"Failed to get existing workspaces|Error loading state|S3.*AccessDenied|backend.*AccessDenied", "Check backend configuration and read-only state access.", ("TF-09",)),
    ("TF_PROVIDER", "terraform", r"Inconsistent dependency lock file|Failed to query available provider packages", "Compare committed lockfile and installed provider metadata.", ("TF-05",)),
    ("PG_CONNECT", "postgresql", r"connection refused|could not connect to server|password authentication failed|too many connections", "Check RDS status, endpoint, network path and redacted connection settings.", ("INC-02", "INC-04", "INC-06")),
    ("RECOVERY_CHECKPOINT", "lifecycle", r"interrupted.*checkpoint|phase.*failed|checkpoint.*running|restore.*already attempted", "Inspect checkpoint stage, immutable provenance and previously completed phases.", ("TF-06",)),
    ("GITHUB_ACTIONS", "ci", r"workflow.*failed|job.*failed|runner.*failed|Process completed with exit code [1-9]", "Inspect failed step and its credential-free log snippet.", ("INC-03",)),
)
INDEX = {ident: (domain, pattern, test, ids) for ident, domain, pattern, test, ids in RULES}

def text_matches(text):
    output = []
    if len(text) > 1_000_000:
        raise ValueError("input too large")
    for ident, domain, pattern, test, scenario_ids in RULES:
        if re.search(pattern, text, re.IGNORECASE):
            output.append(dict(id=ident, domain=domain, confidence="candidate",
                               evidence_based=True, scenario_ids=list(scenario_ids), next_test=test))
    return output

def scenario_rules():
    result = {}
    for ident, _, _, _, scenarios in RULES:
        for scenario in scenarios:
            result.setdefault(scenario, []).append(ident)
    return result
