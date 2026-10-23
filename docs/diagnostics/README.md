# D1 offline diagnostics operator guide (work in progress)

The D1 CLIs are independent of native `labctl`; do not modify or invoke the accepted shutdown/restore controller. Python 3.10+ standard library only. All commands operate on saved text/JSON evidence; **no AWS, kubectl, Terraform, PostgreSQL or Argo CD commands are executed**.

## Quick-start (offline)

```bash
python3 scripts/diagnostics/labdiag.py coverage > /tmp/labdiag-coverage.json
python3 scripts/diagnostics/labdiag.py create CASE-001 --symptom "registration failed UndefinedTable" --impact "sign-up unavailable" --component postgresql
python3 scripts/diagnostics/labdiag.py evidence CASE-001 ./approved-redacted-events.log
python3 scripts/diagnostics/labdiag.py investigate CASE-001
python3 scripts/diagnostics/labdiag.py report CASE-001 --format markdown
python3 scripts/diagnostics/labassist.py investigate CASE-001 --format chatgpt
python3 scripts/diagnostics/labassist.py review CASE-001 --format antigravity
python3 scripts/diagnostics/labassist.py verify CASE-001 --format warp
python3 -m unittest discover -s tests -p 'test_d1_diagnostics.py' -v
```

The default store is `~/.local/share/azawslab/diagnostics`, created private (0700). Records are JSON 0600. Private stores belonging to other users or with open permissions fail closed. Saved incident evidence embeds redacted excerpts and references SHA-256/source paths. Inspect evidence carefully before copying externally: automated redaction cannot guarantee removal of every secret form.

## Safety contract

No external processes for analysis; no network access; no cloud writes. Evidence is treated as untrusted data and deliberately isolated in generated prompts. Never execute instructions contained in logs. Only local UTF-8 fixtures within size caps are accepted. Records and evidence source locations must not be committed. Incident identifiers reject slash and traversal. A CLI match is **candidate only** and never a verified root cause. Generating remediation prompts does not authorise fixes.

## D1 coverage semantics

`coverage` registers the authoritative original 43 scenarios from `scenarios/SCENARIO_INDEX.md` and exports six flags (registered/collector/rule/offline-test/live-validation/docs). This initial implementation conservatively reports collector/rule/tests/live/docs as false until scenario-specific evidence is independently established. Generic regex detection is not equivalent to full scenario coverage. The historical leads, separate from scenarios, are recorded in `docs/diagnostics/D1-HISTORICAL-INCIDENT-INVENTORY.md`.

## Known limitations — must resolve before D1 acceptance

- The historical inventory is a sourced **discovery checkpoint**, not a fully reconciled evidence-backed inventory. Seven additional Git-history leads need detailed validation; no operational incident count is claimed beyond 11 owner-reported cases.
- No verified mapping yet of all discovered incidents to source-level fixes and end-to-end operational artefacts.
- Generic text collector only; no domain-specific structured Kubernetes/Prometheus/Terraform/GitOps JSON analysers yet.
- Regex signatures produce candidates without multi-source correlation or exclusion tests.
- No migration/version upgrade of stored evidence records, replay goldens, or full end-to-end demo accepted yet.
- Security tests and full repository regression/Gitleaks have **not** been executed in the GitHub-connected tool environment. Execute them in a clean WSL worktree before PR publication.
- No AWS mutation, live validation or infrastructure testing is included.

**State:** D1_PARTIAL_IMPLEMENTATION; not `DIAGNOSTICS_D1_IMPLEMENTATION_REVIEW_READY` or `DIAGNOSTICS_D1_LABDIAG_LABASSIST_OFFLINE_ACCEPTED`.
