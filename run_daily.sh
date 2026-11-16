#!/usr/bin/env bash
# Daily run for macOS / Linux. Schedule it with cron (setup.py prints the line), e.g.
#   0 7 * * * /path/to/job-search-kit/run_daily.sh
set -u
cd "$(dirname "$0")"
# cron has a minimal PATH: use the Python setup.py ran with and the folder holding the `claude` CLI.
PY="$(cat .python-path 2>/dev/null || command -v python3 || command -v python)"
[ -f .claude-dir ] && PATH="$(cat .claude-dir):$PATH"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# Skip if today's report already exists.
"$PY" -c "import datetime,pathlib,sys; sys.exit(pathlib.Path('reports',f'{datetime.date.today()}.html').exists())" || exit 0
"$PY" jobsearch.py >> jobsearch_stdout.log 2>&1
