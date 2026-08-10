#!/bin/bash
# The 108 sitl-marked tests, which CI deselects and which have never been run.
# UNBUFFERED to a file, generous limit: a previous attempt used -q with a short
# timeout and produced literally nothing but the word "Terminated".
cd /root/droneserver || exit 2
export PYTHONUNBUFFERED=1
exec timeout 7200 .venv/bin/python -u -m pytest -m sitl -v -p no:cacheprovider \
  --tb=long -rA --durations=25 tests/integration
