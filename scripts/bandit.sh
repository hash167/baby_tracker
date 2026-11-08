#!/bin/sh
# Static security analysis of first-party Python. Config: bandit.yaml.
set -eu
cd "$(dirname "$0")/.."
uvx --python 3.15 --from bandit==1.9.4 bandit \
  -c bandit.yaml \
  -r config tracker tests scripts docker \
  manage.py main.py
