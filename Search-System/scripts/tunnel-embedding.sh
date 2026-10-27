#!/usr/bin/env bash
set -euo pipefail
# Explicit SSH forward on the Mac. Credentials stay in SSH, never in code or logs.
exec ssh -o ExitOnForwardFailure=yes -N -L 127.0.0.1:18081:127.0.0.1:8081 antonius@100.116.20.59
