#!/usr/bin/env bash
# Zip Python Project
# Creates a clean zip of the current Python project:
#   - auto-detects the project's virtualenv (or uses the active one)
#   - runs pip freeze > requirements.txt using that venv
#   - excludes caches, virtualenvs, node_modules, etc. (.git IS included)
# Nothing in your project is deleted or modified (except requirements.txt).
#
# Usage:
#   chmod +x zip-python-project.sh
#   ./zip-python-project.sh

set -e

PIP=""

# 1a. Already-activated venv wins
if [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/pip" ]; then
  PIP="$VIRTUAL_ENV/bin/pip"
  echo "Using active virtualenv: $VIRTUAL_ENV"
else
  # 1b. Otherwise look for a venv folder in the project root
  for d in venv .venv env virtualenv; do
    if [ -x "$d/bin/pip" ]; then
      PIP="$PWD/$d/bin/pip"
      echo "Found virtualenv: ./$d"
      break
    fi
  done
fi

# 1c. Last resort: any folder with pyvenv.cfg, up to two levels deep
if [ -z "$PIP" ]; then
  while IFS= read -r cfg; do
    d=$(dirname "$cfg")
    if [ -x "$d/bin/pip" ]; then
      PIP="$d/bin/pip"
      echo "Found virtualenv: ${d#./}"
      break
    fi
  done < <(find . -maxdepth 3 -name pyvenv.cfg 2>/dev/null)
fi

if [ -z "$PIP" ]; then
  echo "No virtualenv found in this folder."
  echo "Expected one of: venv/  .venv/  env/  -- or activate yours and re-run."
  exit 1
fi

# 2. Freeze dependencies
"$PIP" freeze > requirements.txt
echo "requirements.txt updated ($(wc -l < requirements.txt | tr -d ' ') packages)"

# 3. Zip everything except junk
NAME=$(basename "$PWD")
rm -f "../$NAME.zip"

zip -r "../$NAME.zip" . \
  -x "*/__pycache__/*" "__pycache__/*" "*.pyc" "*.pyo" \
     "*/.pytest_cache/*" ".pytest_cache/*" \
     "*/.mypy_cache/*" ".mypy_cache/*" \
     "*/.ruff_cache/*" ".ruff_cache/*" \
     "*/.ipynb_checkpoints/*" ".ipynb_checkpoints/*" \
     "*/venv/*" "venv/*" "*/.venv/*" ".venv/*" "*/env/*" "env/*" \
     "*/virtualenv/*" "virtualenv/*" \
     "*/.env" ".env" \
     "*/node_modules/*" "node_modules/*" \
     "*.DS_Store" "*.sqlite3" \
  > /dev/null

echo "Done -> ../$NAME.zip"
