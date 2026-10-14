#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' 'BLOCKED: legacy v2 preflight checks were insufficient; implement exact account/cluster ARN/endpoint/backend verification with mocks before enabling.' >&2
exit 78
