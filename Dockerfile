FROM python:3.12-slim

# loadn 引擎镜像：无头跑 agent 任务的干净环境
#   docker build -t loadn .
#   docker run --rm -v "$PWD:/ws" -w /ws \
#     -e ANTHROPIC_BASE_URL -e ANTHROPIC_AUTH_TOKEN \
#     loadn -p --dangerously-skip-permissions "任务描述"
RUN apt-get update && apt-get install -y --no-install-recommends \
      ripgrep git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ws
COPY pyproject.toml README.md LICENSE ./
COPY loadn ./loadn
RUN pip install --no-cache-dir .

# 工具链齐备的镜像里默认开 bypass（容器本身就是边界）；密钥经 env 注入
ENTRYPOINT ["loadn"]
CMD ["--help"]
