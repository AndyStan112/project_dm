FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    DISPLAY=:99 \
    PROJECT_DM_ATTENDED_BROWSER=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fluxbox \
        git \
        novnc \
        procps \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts /usr/local/bin/project-dm-scripts

RUN chmod +x /usr/local/bin/project-dm-scripts/entrypoint.sh

RUN uv sync --frozen --no-dev
RUN uv run playwright install --with-deps chromium

EXPOSE 8000 5900 6080

ENTRYPOINT ["/usr/local/bin/project-dm-scripts/entrypoint.sh"]
