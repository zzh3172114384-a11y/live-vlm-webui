#!/bin/bash
# Start live-vlm-webui with NVIDIA cloud API + local camera support
exec /home/pavs-akm/lg_space/live-vlm-webui/.venv/bin/live-vlm-webui \
  --host 0.0.0.0 \
  --port 8090 \
  --api-base https://integrate.api.nvidia.com/v1 \
  --api-key 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 \
  --model meta/llama-3.2-90b-vision-instruct
