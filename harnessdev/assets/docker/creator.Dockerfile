# Creator sandbox: a general-purpose engineering box.
# Build context is the repository root:  harnessdev build
FROM python:3.12-bookworm

ENV DEBIAN_FRONTEND=noninteractive PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore
RUN apt-get update && apt-get install -y --no-install-recommends \
        git git-lfs curl ca-certificates jq ripgrep less vim-tiny procps build-essential gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code @openai/codex

COPY pyproject.toml README.md LICENSE /opt/harnessdev/
COPY harnessdev /opt/harnessdev/harnessdev
RUN pip install --no-cache-dir "/opt/harnessdev[code]"

# MLE-bench CLI for data-domain self-tests; its leaderboards live in Git LFS
RUN GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 https://github.com/openai/mle-bench /opt/mle-bench \
    && git -C /opt/mle-bench lfs install --local \
    && git -C /opt/mle-bench lfs pull --include "mlebench/competitions/*/leaderboard.csv" \
    && pip install --no-cache-dir -e /opt/mle-bench

WORKDIR /workspace
CMD ["sleep", "infinity"]
