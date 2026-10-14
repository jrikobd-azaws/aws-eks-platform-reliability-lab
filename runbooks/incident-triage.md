# Incident triage checklist
Follow docs/troubleshooting-methodology.md. First checks:
- Pod not Running: describe pod, logs --previous, events.
- Running but not Ready: probes, endpointslices, dependencies.
- 502/504 at ALB: target health, Service/endpoints, backend logs, RDS connectivity/CPU/connections.
- Argo CD OutOfSync/Degraded: app diff, resource health, events.
- Pipeline failure: Actions log, OIDC trust, IAM, Terraform output.
Record evidence BEFORE changing anything.
