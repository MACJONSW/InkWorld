#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_OLLAMA_MODELS=(
  "nomic-embed-text"
  "mxbai-embed-large"
  "bge-m3"
  "embeddinggemma"
)
DEFAULT_ST_MODELS=(
  "BAAI/bge-m3"
  "BAAI/bge-large-zh-v1.5"
  "intfloat/multilingual-e5-large"
  "sentence-transformers/all-MiniLM-L6-v2"
)

PROVIDER="all"  # all | ollama | st
PYTHON_BIN=""
OLLAMA_MODELS=()
ST_MODELS=()
HF_CACHE_DIR="${HF_HOME:-$HOME/.cache/huggingface}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/download_embedding_models.sh [options]

Options:
  --provider <all|ollama|st>       Download source, default: all
  --python <path>                  Python executable for Hugging Face download
  --ollama-models <csv>            Override ollama model list
  --st-models <csv>                Override sentence-transformers model list
  --hf-cache <path>                Hugging Face cache directory
  -h, --help                       Show this help

Examples:
  bash scripts/download_embedding_models.sh --provider all
  bash scripts/download_embedding_models.sh --provider ollama --ollama-models "nomic-embed-text,bge-m3"
  bash scripts/download_embedding_models.sh --provider st --python /data/whr/InkWorld/.venv/bin/python
EOF
}

parse_csv() {
  local csv="$1"
  local -n out_arr=$2
  IFS=',' read -r -a out_arr <<< "$csv"
  for i in "${!out_arr[@]}"; do
    out_arr[$i]="$(echo "${out_arr[$i]}" | xargs)"
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider)
      PROVIDER="${2:-}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    --ollama-models)
      parse_csv "${2:-}" OLLAMA_MODELS
      shift 2
      ;;
    --st-models)
      parse_csv "${2:-}" ST_MODELS
      shift 2
      ;;
    --hf-cache)
      HF_CACHE_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ "$PROVIDER" != "all" && "$PROVIDER" != "ollama" && "$PROVIDER" != "st" ]]; then
  echo "Invalid --provider: $PROVIDER" >&2
  exit 1
fi

if [[ ${#OLLAMA_MODELS[@]} -eq 0 ]]; then
  OLLAMA_MODELS=("${DEFAULT_OLLAMA_MODELS[@]}")
fi
if [[ ${#ST_MODELS[@]} -eq 0 ]]; then
  ST_MODELS=("${DEFAULT_ST_MODELS[@]}")
fi

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "No Python executable found. Use --python to set one." >&2
    exit 1
  fi
fi

download_ollama() {
  if ! command -v ollama >/dev/null 2>&1; then
    echo "[ollama] ollama command not found; skipping ollama model downloads." >&2
    return 0
  fi

  echo "[ollama] Pulling embedding models..."
  for model in "${OLLAMA_MODELS[@]}"; do
    if [[ -n "$model" ]]; then
      echo "[ollama] pull $model"
      ollama pull "$model"
    fi
  done

  echo "[ollama] Installed models (filtered):"
  ollama list | grep -E "nomic-embed-text|mxbai-embed-large|bge-m3|embeddinggemma" || true
}

download_hf_models() {
  echo "[hf] Using Python: $PYTHON_BIN"
  echo "[hf] Cache dir: $HF_CACHE_DIR"

  # socksio is required when the host environment uses a SOCKS proxy.
  "$PYTHON_BIN" -m pip install --quiet --upgrade huggingface_hub socksio

  local models_json
  models_json="$(printf '%s\n' "${ST_MODELS[@]}" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')"

  HF_HOME="$HF_CACHE_DIR" HF_HUB_ENABLE_HF_TRANSFER=1 "$PYTHON_BIN" - <<PY
import json
import os
from huggingface_hub import snapshot_download

models = json.loads('''$models_json''')
for repo_id in models:
    print(f"[hf] Downloading {repo_id} ...")
    snapshot_download(
        repo_id=repo_id,
        local_files_only=False,
    )
print("[hf] Done.")
PY
}

if [[ "$PROVIDER" == "all" || "$PROVIDER" == "ollama" ]]; then
  download_ollama
fi

if [[ "$PROVIDER" == "all" || "$PROVIDER" == "st" ]]; then
  download_hf_models
fi

echo "All requested embedding model downloads are complete."
