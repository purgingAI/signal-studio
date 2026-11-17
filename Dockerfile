FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
# Claude Code CLI (native build): the agent runs `claude -p` on your subscription via CLAUDE_CODE_OAUTH_TOKEN.
RUN curl -fsSL https://claude.ai/install.sh | bash && ln -sf /root/.local/bin/claude /usr/local/bin/claude && claude --version
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY web ./web
ENV PYTHONUNBUFFERED=1 TZ=Asia/Dubai
EXPOSE 8099
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--proxy-headers"]
