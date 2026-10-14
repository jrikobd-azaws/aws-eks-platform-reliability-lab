# Scenario: <ID - Name>

| Field | Value |
|---|---|
| ID / Wave / Depth | |
| Date executed | |
| Cluster / Kubernetes version | |
| Operator | human |

## Purpose
## Prerequisites
(add-ons, policies, tools that must exist; link to the validate-up output proving the system was healthy)
## Blast radius and abort condition
(what can this break? maximum tolerated outage? who aborts and how?)
## Initial State
(attach pre-injection snapshot path)
## Failure Injection
(exact script path; reversible; scoped to lab namespaces or sandbox)
## User / Service Impact
## Initial Hypotheses
## Triage
(commands in order and why; evidence gathered BEFORE any change)
## Evidence
(real, sanitised output; evidence file paths; no account IDs, tokens or secrets)
## Detection
(what signal should have alerted before a user noticed? was it present? gap?)
## Root Cause
## Remediation
(smallest controlled change; script path)
## Recovery Validation
(technical and service/user validation; attach validate-up output)
## Rollback
## Prevention
## Cost and time
## Differences from plan
(anything that did not behave as the index predicted)
## Lessons Learned
## Definition of done
- [ ] Injection and recovery scripts exist and were run
- [ ] Evidence chain complete: failure -> evidence -> root cause -> fix -> recovery
- [ ] Evidence path recorded in SCENARIO_INDEX.md
- [ ] `scripts/check-all.sh` passes
- [ ] Sanitised for publication
