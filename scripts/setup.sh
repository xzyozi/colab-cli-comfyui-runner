#!/usr/bin/env bash
# Colab VM側 汎用環境復元スクリプト（C-002 / モデル非依存）
# 終了コード 2: Google Drive 上の必須モデル資産が不足 (ERR-SETUP-DRIVE-MISSING)

set -euo pipefail

MODEL_NAME=""
DRIVE_ROOT="ComfyUI"
SETUP_DIR="/tmp/ccr_setup"
ASSETS_FILE=""
COMFY_DIR="${COMFY_DIR:-/content/ComfyUI}"
COMFY_PORT="${COMFY_PORT:-8188}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-name)
      MODEL_NAME="$2"
      shift 2
      ;;
    --drive-root)
      DRIVE_ROOT="$2"
      shift 2
      ;;
    --setup-dir)
      SETUP_DIR="$2"
      shift 2
      ;;
    --assets-file)
      ASSETS_FILE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

DRIVE_BASE="/content/drive/MyDrive/${DRIVE_ROOT}"

if [[ ! -d "/content/drive/MyDrive" ]]; then
  echo "Google Drive is not mounted at /content/drive/MyDrive" >&2
  exit 2
fi

if [[ ! -d "${DRIVE_BASE}" ]]; then
  echo "Drive root directory not found: ${DRIVE_BASE}" >&2
  exit 2
fi

if [[ -n "${ASSETS_FILE}" && -f "${ASSETS_FILE}" ]]; then
  while IFS= read -r rel_asset || [[ -n "${rel_asset}" ]]; do
    [[ -z "${rel_asset}" || "${rel_asset}" =~ ^# ]] && continue
    if [[ ! -e "${DRIVE_BASE}/${rel_asset}" ]]; then
      echo "Required Drive asset missing: ${DRIVE_BASE}/${rel_asset}" >&2
      exit 2
    fi
  done < "${ASSETS_FILE}"
fi

# uv と comfy-cli による高速構築
if ! command -v uv >/dev/null 2>&1; then
  pip install -q uv
fi
if ! command -v comfy >/dev/null 2>&1; then
  uv pip install --system -q comfy-cli
fi

if [[ ! -d "${COMFY_DIR}" ]]; then
  if ! comfy --skip-prompt --workspace="${COMFY_DIR}" install --nvidia --fast-deps; then
    rm -rf "${COMFY_DIR}"
    git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git "${COMFY_DIR}"
    uv pip install --system -q -r "${COMFY_DIR}/requirements.txt"
  fi
fi

# モデル固有の追加 Python 依存適用（workflows/<model_name>/requirements.txt）
if [[ -f "${SETUP_DIR}/requirements.txt" ]]; then
  uv pip install --system -q -r "${SETUP_DIR}/requirements.txt"
fi

# モデル固有のカスタムノード導入（workflows/<model_name>/nodes.txt）
if [[ -f "${SETUP_DIR}/nodes.txt" ]]; then
  mkdir -p "${COMFY_DIR}/custom_nodes"
  while IFS= read -r repo_url || [[ -n "${repo_url}" ]]; do
    [[ -z "${repo_url}" || "${repo_url}" =~ ^# ]] && continue
    repo_name="$(basename "${repo_url}" .git)"
    target_node_dir="${COMFY_DIR}/custom_nodes/${repo_name}"
    if [[ ! -d "${target_node_dir}" ]]; then
      git clone --depth 1 "${repo_url}" "${target_node_dir}"
    fi
    if [[ -f "${target_node_dir}/requirements.txt" ]]; then
      uv pip install --system -q -r "${target_node_dir}/requirements.txt"
    fi
  done < "${SETUP_DIR}/nodes.txt"
fi

# モデル・入出力ディレクトリのシンボリックリンク構築
mkdir -p "${COMFY_DIR}/models" "${COMFY_DIR}/input"
for subdir in diffusion_models text_encoders vae loras embeddings; do
  if [[ -d "${DRIVE_BASE}/models/${subdir}" ]]; then
    rm -rf "${COMFY_DIR}/models/${subdir}"
    ln -sfn "${DRIVE_BASE}/models/${subdir}" "${COMFY_DIR}/models/${subdir}"
  fi
done

mkdir -p "${DRIVE_BASE}/output"
rm -rf "${COMFY_DIR}/output"
ln -sfn "${DRIVE_BASE}/output" "${COMFY_DIR}/output"

if [[ -d "${DRIVE_BASE}/input" ]]; then
  rm -rf "${COMFY_DIR}/input/drive_assets"
  ln -sfn "${DRIVE_BASE}/input" "${COMFY_DIR}/input/drive_assets"
fi

# 既にポート待ち受け中であれば再起動せず維持、未起動ならバックグラウンド起動
if ! pgrep -f "main.py.*--port ${COMFY_PORT}" >/dev/null 2>&1; then
  nohup python3 "${COMFY_DIR}/main.py" --listen 127.0.0.1 --port "${COMFY_PORT}" \
    > /tmp/comfyui.log 2>&1 < /dev/null &
fi

echo "Setup complete for model=${MODEL_NAME}"
