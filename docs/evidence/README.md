# Evidence handling

Commit only **reviewed, sanitised** and necessary evidence in this directory. Keep unreviewed Kubernetes/AWS output, logs, exact resource inventories, credentials, tokens, private identifiers and temporary state in `.private-evidence/` or `.runtime-inventory/` (gitignored). No `done` scenario state without real observed injection, diagnosis and recovery evidence. Documentation copied from a tutorial is learning material, not proof that your EKS lab ran.
