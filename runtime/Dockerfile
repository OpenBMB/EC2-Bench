FROM node:22-bookworm
ARG OPENCLAW_VERSION
RUN test -n "$OPENCLAW_VERSION"
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv git curl ca-certificates wget jq unzip \
    ffmpeg libreoffice chromium fonts-dejavu fonts-noto-cjk \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /root/miniconda3/envs/eval \
    && /root/miniconda3/envs/eval/bin/pip install --no-cache-dir \
       numpy==1.26.4 pillow requests openai==2.6.1 json-repair fastapi uvicorn 'httpx[http2]' \
       pyyaml pandas openpyxl python-docx python-pptx pymupdf weasyprint \
       scipy matplotlib beautifulsoup4 lxml playwright torch torchvision einops \
       timm ftfy regex iopath pycocotools opencv-python-headless
ENV PATH="/root/miniconda3/envs/eval/bin:${PATH}"
RUN npm install -g "openclaw@${OPENCLAW_VERSION}" agent-browser \
    && openclaw --version
RUN mkdir -p /tmp_workspace /root/.openclaw/workspace /root/skills \
    && printf '%s\n' '{"gateway":{"mode":"local","auth":{"mode":"token","token":"ec2-review-local"}},"agents":{"defaults":{"workspace":"/tmp_workspace"}}}' > /root/.openclaw/openclaw.json
WORKDIR /root
CMD ["bash"]
