# ADR-001 Two Terraform stacks, ephemeral lab
Status: Accepted. Context: about USD 160 budget, 3.5 h sessions; EKS, NAT, RDS and ALB bill hourly.
Decision: permanent stack kept; lab stack created at session start and destroyed at the end.
Consequences: lower cost; rebuild time is part of each session; Prometheus/Grafana data lost so export evidence first; RDS reseeded by script.
