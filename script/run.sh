

# edge-only
python3 eval/run_batch.py \
  --category ACE_Bench --parallel 16 \
  --edge-model vllm/Qwen/Qwen3.5-27B \
  --models-config my_api.json \
  --output-dir output/edge_only/qwen3.5-27b --repeat 3

# cloud-only
python3 eval/run_batch.py \
  --category ACE_Bench --parallel 16 \
  --cloud-model your-provider/gpt-5.4 \
  --models-config my_api.json \
  --output-dir output/cloud_only/gpt5.4 --repeat 3

# edge-cloud

# sketch-guide
python3 eval/run_batch.py \
  --category ACE_Bench --parallel 8 \
  --edge-model vllm/Qwen/Qwen3.5-27B --cloud-model your-provider/gpt-5.4 --models-config my_api.json \
  --run-mode pipeline-plan-executor --privacy-judge-mode off \
  --output-dir output/sketch-guide/qwen3.5-27b_to_gpt5.4 --repeat 3


# task-router
python3 eval/run_batch.py \
  --category ACE_Bench --parallel 8 \
  --edge-model vllm/Qwen/Qwen3.5-27B --cloud-model your-provider/gpt-5.4 --models-config my_api.json \
  --run-mode query-router \
  --query-router-table utils/irt_router/qr_routes_gpt54_to_qwen35_27b_p25.json \
  --output-dir output/task-router/qwen3.5-27b_to_gpt5.4 --repeat 3

# step-router
GLIMPSE_ENTROPY_THRESHOLD=1.5 \
python3 eval/run_batch.py \
  --category ACE_Bench --parallel 8 \
  --edge-model vllm/Qwen/Qwen3.5-27B --cloud-model your-provider/gpt-5.4 --models-config my_api.json \
  --run-mode step-router --no-redact \
  --output-dir output/step-router/qwen3.5-27b_to_gpt5.4 --repeat 3


# adaptive assistant
python3 eval/run_batch.py \
  --category ACE_Bench --parallel 8 \
  --edge-model vllm/Qwen/Qwen3.5-27B --cloud-model your-provider/gpt-5.4 --models-config my_api.json \
  --run-mode advisor \
  --output-dir output/adaptive-assistant/qwen3.5-27b_to_gpt5.4 --repeat 3
