# D1 offline diagnostics operator guide (work in progress)

The D1 CLIs are independent of native `labctl`; do not modify or invoke the accepted shutdown/restore controller. Python 3.10+ standard library only. All commands operate on saved text/JSON evidence; **no AWS, kubectl, Terraform, PostgreSQL or Argo CD commands are executed**.

## Quick-start (offline)

```bash
python3 scripts/diagnostics/labdiag.py coverage > /tmp/labdiag-coverage.json
python3 scripts/diagnostics/labdiag.py inventory
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

## Verified D1 implementation and evidence boundaries

- Shared versioned incident JSON schema with private 0700 store and 0600 record files.
- Incident create, show, update, investigate, reopen/close, evidence collection, sanitised JSON/Markdown reporting, replay, historical inventory and 43-scenario coverage.
- Text and bounded structured-JSON offline collectors across Kubernetes, AWS/EKS/IAM signals, Helm/Argo CD, Prometheus/Alertmanager, PostgreSQL, Terraform, CI and lifecycle metadata. Candidate signal patterns are non-causal; additional cross-layer correlations remain explicitly unverified.
- Three `labassist` prompt modes for ChatGPT, Antigravity, Warp and generic models; supplied evidence is marked untrusted and only bounded redacted excerpts are included.
- Four controlled synthetic replay fixtures with expected-signature assertions. A failing expected replay signature produces an explicit non-zero exit.
- Historical registry: 11 owner-reported cases and seven commit-backed defect candidates. Evidence-qualified classifications and source cross-references are in `D1-HISTORICAL-INCIDENT-INVENTORY.md`, and the machine-readable register is `scenarios/D1_HISTORICAL_INVENTORY.json`.
- Full 43-ID matrix: `D1-SCENARIO-COVERAGE.md`. All 43 are registered; 28 have a candidate textual signal-rule link. No scenario-specific complete regression or live validation is claimed.
- Dedicated GitHub Actions D1 tests and repository `safety-checks` (which includes Gitleaks and existing offline checks). Inspect actual latest commit checks before accepting PR.

## Known limitations and D2/D3 handoff

Historical operational logs and some private lifecycle evidence live outside GitHub. They were not fetched, changed or disclosed for D1. As a result, incident occurrence and root-cause confidence are not automatically certified merely by an owner report or fix commit. Candidate defects may overlap owner-reported cases and must remain separate until evidence establishes uniqueness.

D1 is not a production-ready incident AI agent. Detection currently uses bounded patterns and representative offline structured parsers, not a complete semantic inference engine. AI prompts are generated for human review and may need manual evidence selection. Redaction is best effort; inspect generated material before sharing externally. No autonomous fixes, live collectors, infrastructure mutations or scenario injection are included.

A `D1_IMPLEMENTATION_REVIEW_READY` status means the offline toolkit is demonstrable with passing CI and documented limitations. It does not mean all 43 live troubleshooting scenarios have been executed. The full scenario waves, live validation and publication belong to D2/D3.

## D1 corrective acceptance: operator contracts

### Incident closure

`labdiag update ID --state closed` is **blocked** unless the current state is `verification`, an explicit verification review note was recorded using `update ID --state verification --note "..."`, and every referenced original evidence file is still present with its original SHA-256. Those conditions provide a minimum audit gate; they do not independently establish that the remediation actually worked. The operator is accountable for the verification note and should attach post-remediation evidence. Use `update ID --state closed --note "Reason for administrative closure"` for documented **administrative, unresolved** closure. Never interpret this path as resolution. `reopened` clears closure status.

### Evidence integrity and prompts

`show`, `report`, `investigate` and `labassist` rehash original offline files where applicable. Removed, symlinked or changed sources are `unverifiable` or `changed`, never `verified`; changed excerpts are withheld from AI prompts. Candidate signatures and correlations remain unverified. Redaction handles common credentials, database URI passwords, bearer tokens, nested metadata and multiline private-key blocks but is **best effort**, not a promise to detect every secret syntax. Inspect exported logs and model prompts before sharing.

### Investigation depth and AI-target presets: explicit D1 deferral

`quick`, `standard`, and `deep` are **not implemented D1 CLI profiles**. All D1 investigations use the single deterministic, bounded offline path; unsupported `--profile` requests are rejected by argparse. The `--format {chatgpt,antigravity,warp,generic}` choices currently select target **labels only**, not different AI model instruction presets or external API calls. Meaningful prompt presets and depth-specific evidence budgets are deferred to D2 after independent product and security review. No documentation or acceptance result should imply profiles or per-model reasoning policies exist in D1.
