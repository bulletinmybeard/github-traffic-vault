# Build stage: resolve the pinned lockfile into a requirements list so the
# runtime image installs exact versions (not floating caret ranges) and
# stays poetry-free.
FROM python:3.12-slim AS deps

WORKDIR /app

RUN pip install --no-cache-dir poetry poetry-plugin-export

COPY pyproject.toml poetry.lock ./
RUN poetry export --only main --without-hashes -f requirements.txt -o requirements.txt


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
  && apt-get install -y --no-install-recommends gh git \
  && rm -rf /var/lib/apt/lists/*

# Pinned dependencies first (cached layer), then the project itself with
# --no-deps so pip doesn't re-resolve anything off the lock.
COPY --from=deps /app/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY github_traffic_vault ./github_traffic_vault
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8800

# Default CMD = serve the web UI. The remote cron triggers sync via
# `docker exec github-traffic-vault github-traffic-vault sync`, bypassing this CMD.
CMD ["github-traffic-vault", "serve", "--host", "0.0.0.0", "--port", "8800"]
