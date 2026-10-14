#!/usr/bin/env bash

# Legacy v2 helper library is disabled.
# Do not enable until the v3 replacement is approved and tested.

set -euo pipefail

printf '%s\n' \
  "BLOCKED: v2 helper library retired pending reviewed v3 replacement." >&2

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    exit 78
else
    return 78
fi
