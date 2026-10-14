# Access model — human execution versus plan-only AI

| Identity | Used by | Intended authority | Current status |
|---|---|---|---|
| Dedicated workload-account human execution role | Owner's deliberately isolated terminal | Human-reviewed saved-plan apply/destroy and individually approved cluster writes | NOT YET VERIFIED |
| `lab-plan-only` role/profile | Copilot/agent shell where necessary | Read/plan only; limited backend lock rights only if proven necessary; no AWS resource mutation | NOT YET VERIFIED |
| GitHub Actions OIDC role | CI checks / Terraform planning / ECR delivery when designed | Least privilege, trust scoped to approved repository/branch/workflow; manual approval for actual writes | NOT YET IMPLEMENTED |

`LAB_HUMAN_APPROVED=yes`, typed prompts and CODEOWNERS are **friction/audit mechanisms, not security boundaries**. Do not ask AI agents to set them or access an administrator profile. Every remote write is separately approved by the human; GitHub approval controls depend on the user's actual plan and repository visibility. Keep terminal auto-approval off; never install credentials in agent-accessible environment variables or cached profiles. Exact cluster ARN/endpoint, account, backend and permissions require runtime verification before write-capable tooling is implemented.
