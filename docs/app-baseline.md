# Application baseline
Source: `fastapi/full-stack-fastapi-template`, pinned commit recorded below. Do not track an unpinned branch.

```
upstream_repository: https://github.com/fastapi/full-stack-fastapi-template
upstream_commit:     TBD
clone_date:          TBD
licence:             TBD (check LICENSE)
known_default_credentials: TBD (list and replace all)
```
## Principle
The application is not the project; the platform is. Keep the app nearly unchanged.

## Images
- `backend`: FastAPI from the template's backend Dockerfile (verify at pinned commit).
- `frontend`: React/Vite served by Nginx from the template's frontend Dockerfile (verify at pinned commit).
Both: non-root, pinned base image, no baked secrets, health check, git-commit label, never the `latest` tag.

## Permitted changes (only these)
1. `GET /health/live` (no dependency checks) and `GET /health/ready` (database query; 503 on failure).
2. `GET /metrics` (Prometheus), reachable only inside the cluster.
3. Environment-driven configuration (DB host/user/password, CORS origins, secret key).
4. Deterministic idempotent seed script (fixed IDs) to reseed RDS each session.
5. Later (INC-06 only): one deliberately heavy endpoint or bulk dataset for slow-query behaviour.

## Not permitted
New business features, entities or auth schemes. Procurement-domain work belongs to the migration project.

## Extra workloads for diagnostic value (Helm chart)
`db-seed` Job (Helm hook); a small `notify-worker` and an HPA on the backend, both added when the scenarios that need them are built (Waves 3 and 6 at the latest); frontend with readOnlyRootFilesystem and explicit writable mounts.

## Sizing
Requests/limits deliberately small so Pending and OOMKilled scenarios trigger reliably. Record measured `kubectl top` values in docs/evidence.
