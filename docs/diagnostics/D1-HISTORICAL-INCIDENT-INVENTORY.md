# D1 Historical Incident Inventory — Discovery Checkpoint

Status: INITIAL_SOURCE_CORROBORATION; not final root-cause verification.

Baseline: `main` at `0bd7e389e674d5f2ca0f5efb518a01f99fec8829`. This file contains **11 owner-reported incident leads** and **7 additional commit-backed defect candidates**. The latter must be validated against source, tests and private evidence before promoting to CONFIRMED_INCIDENT. Historical events and 43 planned failure-injection scenarios are tracked separately.

## Owner-reported historical incident leads (11)

| ID | Reported symptom / failure | Related scenario IDs (mapping candidates, not proof) | Initial evidence / pending validation |
|---|---|---|---|
| HIST-001 | HTTP 500 on registration: missing Alembic users table | K8S-05,INC-02 | User report; schema validation source not yet inspected |
| HIST-002 | Argo CD AppProject rejected Prometheus Helm source | ARGO-03,ARGO-05 | User report; GitOps AppProject changes to inspect |
| HIST-003 | Prometheus Operator CRDs exceeded annotation limits | ARGO-05,HELM-03 | User report; commit 6269558170ab34a89acd0d7ae0d1c4d1e66e5cfe |
| HIST-004 | Prometheus Operator TLS/admission Secret mismatch | HELM-01,K8S-03 | User report; commit a4dfa2796440ec39f7a372b9b5a4f401fd7467bb |
| HIST-005 | node-exporter blocked by baseline Pod Security Admission | K8S-03,HELM-01 | User report; commit a4dfa2796440ec39f7a372b9b5a4f401fd7467bb |
| HIST-006 | Argo CD application controller OOMKilled (exit 137) | K8S-04,ARGO-02 | User report; platform bootstrap change to inspect |
| HIST-007 | Alertmanager route referred to undefined null receiver | HELM-01,ARGO-02 | User report; commit 156f790962d531152b31b5d1b7a8df3c63e38e56 |
| HIST-008 | FastAPI ServiceMonitor target absent due to selector and ownership | K8S-06,ARGO-01 | User report; commits 8b3ff9efc481984a0bad9e17c50f4465150e0ad6 and cac0869d03f142793a1926885a4411d141704713 |
| HIST-009 | Bootstrap AppProject rejected ServiceMonitor ownership | ARGO-05,ARGO-03 | User report; commit 42324a0de363ea645cbd52934e2f4aec27ed2281 |
| HIST-010 | Eight obsolete node-exporter resources caused OutOfSync | ARGO-01 | User report; cleanup evidence to inspect |
| HIST-011 | Automation assumed mutable Helm, endpoint and GitOps values | TF-02,HELM-01 | User report; distinct defects require independent evidence |

## Additional repository-history defect candidates (7)

| ID | Finding | Git commit | Classification pending |
|---|---|---|---|
| CAND-012 | Native PostgreSQL restore TLS hardening | [af495717a5a0](https://github.com/jrikobd-azaws/aws-eks-platform-reliability-lab/commit/af495717a5a073d0cf84d798fdded9e73af46ce9) | ENGINEERING_DEFECT candidate; exact failure and fix to verify |
| CAND-013 | Backup provenance/source revision validation defects | [355199fbef18](https://github.com/jrikobd-azaws/aws-eks-platform-reliability-lab/commit/355199fbef1812eabb74e19142bbb202563f0d0c) | ENGINEERING_DEFECT candidate; exact failure and fix to verify |
| CAND-014 | Failed backup rollover and evidence boundary defect | [d02d1562898f](https://github.com/jrikobd-azaws/aws-eks-platform-reliability-lab/commit/d02d1562898f2f0d2160defbb81e81840e5cd458) | ENGINEERING_DEFECT candidate; exact failure and fix to verify |
| CAND-015 | LB3 shutdown plan path defect | [90d49c0b5c6a](https://github.com/jrikobd-azaws/aws-eks-platform-reliability-lab/commit/90d49c0b5c6a38c335e2b861829d3e4b45e0270b) | ENGINEERING_DEFECT candidate; exact failure and fix to verify |
| CAND-016 | Shutdown readiness private-evidence hashing defect | [6ecbee7136cc](https://github.com/jrikobd-azaws/aws-eks-platform-reliability-lab/commit/6ecbee7136cc7c7119f64e68cc219af7579d7d62) | ENGINEERING_DEFECT candidate; exact failure and fix to verify |
| CAND-017 | Native DB restore diagnostic redaction | [7de2e1ad5066](https://github.com/jrikobd-azaws/aws-eks-platform-reliability-lab/commit/7de2e1ad5066cf9afdd228d8a5edc0701735e9ee) | ENGINEERING_DEFECT candidate; exact failure and fix to verify |
| CAND-018 | CRD YAML serialization defect | [2060684a4321](https://github.com/jrikobd-azaws/aws-eks-platform-reliability-lab/commit/2060684a43218260b11ade74eb60643be834ea24) | ENGINEERING_DEFECT candidate; exact failure and fix to verify |

## Evidence / classification contract

- `CONFIRMED_INCIDENT`: observed operational failure backed by source-linked run logs or trustworthy historical evidence.
- `ENGINEERING_DEFECT`: source-level correction corroborated by code/history, but operational occurrence not established.
- `TEST_SCENARIO`: planned or simulated failure from `scenarios/SCENARIO_INDEX.md`.
- `POTENTIAL_FAILURE_MODE`: plausible condition not shown to have occurred.
- `INSUFFICIENT_EVIDENCE`: owner recollection or incomplete evidence; request review without manufacturing proof.

These 18 leads are **not 18 independently verified operational incidents**. Avoid double counting HIST-004/HIST-005 or HIST-008/HIST-009 until evidence disambiguates; commit fixes may overlap owner-reported cases.

## Coverage gap

The authoritative `scenarios/SCENARIO_INDEX.md` lists 43 stable IDs across six waves; all are `planned` on this baseline. Catalogue registration does not establish collector, detector, regression, live-validation or publication coverage. D1 needs one row per ID with those separate coverage flags. No live validation is claimed.

## Unresolved discovery

Inspect source diffs, tests, deployment documentation and historical private evidence (locally and only with authorisation). Add verified incident timestamps, affected components, actual diagnostic evidence, root-cause confidence, remediation validation and duplicate links. Check GitHub Actions and infrastructure incidents beyond this first set. Do not expose private evidence or secrets.
