#!/usr/bin/env bash
# Colab CPUランタイム等から MiniMax H3 公式モデル群を Google Drive へ事前配置するスクリプト
# 使い方: bash stage_minimax_h3.sh [--drive-root ComfyUI] [--with-ref2va]

set -euo pipefail

DRIVE_ROOT="ComfyUI"
WITH_REF2VA=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --drive-root)
      DRIVE_ROOT="$2"
      shift 2
      ;;
    --with-ref2va)
      WITH_REF2VA=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

DRIVE_BASE="/content/drive/MyDrive/${DRIVE_ROOT}"
if [[ ! -d "/content/drive/MyDrive" ]]; then
  echo "Google Drive is not mounted at /content/drive/MyDrive. Run 'colab drivemount' first." >&2
  exit 2
fi

mkdir -p \
  "${DRIVE_BASE}/models/diffusion_models" \
  "${DRIVE_BASE}/models/text_encoders" \
  "${DRIVE_BASE}/models/vae" \
  "${DRIVE_BASE}/models/loras" \
  "${DRIVE_BASE}/models/embeddings" \
  "${DRIVE_BASE}/input" \
  "${DRIVE_BASE}/output"

download_file() {
  local url="$1"
  local dest="$2"
  local min_bytes="$3"

  if [[ -f "${dest}" ]]; then
    local existing_size
    existing_size="$(stat -c%s "${dest}")"
    if [[ "${existing_size}" -ge "${min_bytes}" ]]; then
      echo "[SKIP] Already present (${existing_size} bytes): ${dest}"
      return 0
    fi
    echo "[RESUME] Partial file found (${existing_size} bytes): ${dest}"
  fi

  echo "[DOWNLOAD] ${url} -> ${dest}"
  curl -fL --retry 5 --retry-delay 5 -C - -o "${dest}.part" "${url}"
  mv -f "${dest}.part" "${dest}"

  local final_size
  final_size="$(stat -c%s "${dest}")"
  if [[ "${final_size}" -lt "${min_bytes}" ]]; then
    echo "[ERROR] Downloaded file smaller than expected (${final_size} < ${min_bytes}): ${dest}" >&2
    exit 3
  fi
  echo "[OK] ${dest} (${final_size} bytes)"
}

BASE_URL="https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main"

# 1. Diffusion model (T2V / FL2V, 20.97 GB)
download_file \
  "${BASE_URL}/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors" \
  "${DRIVE_BASE}/models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors" \
  20000000000

# 2. Text Encoder (Qwen3-VL 32B NVFP4 AWQ, 15.69 GB)
download_file \
  "${BASE_URL}/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" \
  "${DRIVE_BASE}/models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" \
  15000000000

# 3. Video VAE (official template uses int8_convrot 2.81 GB) & Audio VAE (fp32 605 MB)
download_file \
  "${BASE_URL}/vae/minimax_h3_video_vae_int8_convrot.safetensors" \
  "${DRIVE_BASE}/models/vae/minimax_h3_video_vae_int8_convrot.safetensors" \
  2700000000

download_file \
  "${BASE_URL}/vae/minimax_h3_audio_vae_fp32.safetensors" \
  "${DRIVE_BASE}/models/vae/minimax_h3_audio_vae_fp32.safetensors" \
  550000000

# 4. Turbo 8step LoRA (1.96 GB)
download_file \
  "${BASE_URL}/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors" \
  "${DRIVE_BASE}/models/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors" \
  1900000000

# 5. Optional Ref2VA diffusion model (20.97 GB)
if [[ "${WITH_REF2VA}" -eq 1 ]]; then
  download_file \
    "${BASE_URL}/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors" \
    "${DRIVE_BASE}/models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors" \
    20000000000
fi

echo "=== Drive staging complete ==="
ls -lh \
  "${DRIVE_BASE}/models/diffusion_models" \
  "${DRIVE_BASE}/models/text_encoders" \
  "${DRIVE_BASE}/models/vae" \
  "${DRIVE_BASE}/models/loras"
