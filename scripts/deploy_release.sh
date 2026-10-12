#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/deploy_release.sh --release PATH --commit SHA [options]

Options:
  --deploy-root PATH     Default: /home/aicontrol/smart-home-deploy
  --current PATH         Default: <deploy-root>/current
  --shared PATH          Default: <deploy-root>/shared
  --dry-run              Print actions without changing files
  --prepare-only         Validate and prepare directories only; do not switch symlink
  --run-tests            Run syntax/unit checks in release
  --migrate              Backup DB and run migration; not enabled by default
  --help                 Show this help

This script never restarts services by default.
USAGE
}

DEPLOY_ROOT="/home/aicontrol/smart-home-deploy"
RELEASE=""
COMMIT=""
CURRENT=""
SHARED=""
DRY_RUN=0
PREPARE_ONLY=0
RUN_TESTS=0
RUN_MIGRATION=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy-root) DEPLOY_ROOT="$2"; shift 2 ;;
    --release) RELEASE="$2"; shift 2 ;;
    --commit) COMMIT="$2"; shift 2 ;;
    --current) CURRENT="$2"; shift 2 ;;
    --shared) SHARED="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --prepare-only) PREPARE_ONLY=1; shift ;;
    --run-tests) RUN_TESTS=1; shift ;;
    --migrate) RUN_MIGRATION=1; shift ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$RELEASE" || -z "$COMMIT" ]]; then
  echo "--release and --commit are required" >&2
  usage
  exit 2
fi

CURRENT="${CURRENT:-$DEPLOY_ROOT/current}"
SHARED="${SHARED:-$DEPLOY_ROOT/shared}"

run() {
  local cmd=("$@")
  printf '+'
  for arg in "${cmd[@]}"; do
    printf ' %q' "$arg"
  done
  printf '\n'
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "${cmd[@]}"
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Required file missing: $path" >&2
    exit 1
  fi
}

require_dir() {
  local path="$1"
  if [[ ! -d "$path" ]]; then
    echo "Required directory missing: $path" >&2
    exit 1
  fi
}

echo "release=$RELEASE"
echo "commit=$COMMIT"
echo "deploy_root=$DEPLOY_ROOT"
echo "shared=$SHARED"
echo "current=$CURRENT"
echo "dry_run=$DRY_RUN prepare_only=$PREPARE_ONLY run_tests=$RUN_TESTS migrate=$RUN_MIGRATION"

require_dir "$RELEASE"
require_file "$RELEASE/ai-agent/api.py"
require_file "$RELEASE/smart_home_agent/agent_core/orchestrator.py"
require_file "$RELEASE/smart_home_agent/storage/sqlite.py"
require_file "$RELEASE/chatbot-gateway/index.js"
require_file "$RELEASE/scripts/migrate.py"
require_file "$RELEASE/package.json"

if command -v git >/dev/null 2>&1 && [[ -d "$RELEASE/.git" ]]; then
  actual_commit="$(git -C "$RELEASE" rev-parse HEAD)"
  if [[ "$actual_commit" != "$COMMIT" ]]; then
    echo "Release commit mismatch: expected $COMMIT got $actual_commit" >&2
    exit 1
  fi
fi

run mkdir -p "$DEPLOY_ROOT/releases" "$SHARED/config" "$SHARED/data" "$SHARED/logs" "$SHARED/runtime" "$SHARED/secrets"

if [[ -e "$SHARED/data/smart_home.db" ]]; then
  echo "database_exists=true"
else
  echo "database_exists=false"
fi

if [[ "$RUN_TESTS" -eq 1 ]]; then
  echo "running release tests"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    (
      cd "$RELEASE"
      export PYTHONPATH=.
      python tests/test_core.py
      npm test
      python -m compileall smart_home_agent scripts tests ai-agent
      node --check chatbot-gateway/index.js
    )
  fi
fi

if [[ "$RUN_MIGRATION" -eq 1 ]]; then
  db="$SHARED/data/smart_home.db"
  require_file "$db"
  backup_dir="$SHARED/data/backups"
  backup_file="$backup_dir/smart_home-$(date -u +%Y%m%dT%H%M%SZ).db"
  run mkdir -p "$backup_dir"
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "sqlite3 is required for safe backup before migration" >&2
    exit 1
  fi
  run sqlite3 "$db" ".backup '$backup_file'"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    (cd "$RELEASE" && python scripts/migrate.py --db "$db")
  else
    echo "+ python scripts/migrate.py --db $db"
  fi
fi

if [[ "$PREPARE_ONLY" -eq 1 ]]; then
  echo "prepare_only=true; symlink not changed; services not restarted"
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "+ ln -sfn $RELEASE $CURRENT"
else
  ln -sfn "$RELEASE" "$CURRENT"
fi

echo "symlink_switch_complete=true"
echo "services_restarted=false"
