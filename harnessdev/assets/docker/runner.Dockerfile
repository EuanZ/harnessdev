# Task container for benchmarks that ship no image of their own (writing, search).
FROM python:3.12-slim-bookworm
ENV PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir requests httpx beautifulsoup4 lxml
WORKDIR /workspace
CMD ["sleep", "infinity"]
