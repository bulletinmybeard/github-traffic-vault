FROM python:3.12-slim

WORKDIR /app

# `gh` lets the container fall back to `gh auth token`
# if the GITHUB_TOKEN is not set in the environment.
RUN apt-get update \
  && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
  && mkdir -p /etc/apt/keyrings \
  && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends gh \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY github_traffic_vault ./github_traffic_vault

RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -e .

EXPOSE 8800

# Default CMD = serve the web UI. The remote cron triggers sync via
# `docker exec github-traffic-vault github-traffic-vault sync`, bypassing this CMD.
CMD ["github-traffic-vault", "serve", "--host", "0.0.0.0", "--port", "8800"]
