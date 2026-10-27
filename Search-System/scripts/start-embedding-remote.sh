#!/usr/bin/env bash
set -euo pipefail
# One-time copy: scp scripts/start-embedding-server.sh antonius@100.116.20.59:~/ai/start-search-embedding.sh
# Interactive SSH handles the user's password; no credentials are stored here.
exec ssh -t antonius@100.116.20.59 'bash ~/ai/start-search-embedding.sh'
