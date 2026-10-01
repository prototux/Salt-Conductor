# Salt Conductor - production image
FROM python:3.12-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="Salt Conductor" \
      org.opencontainers.image.description="Web console for SaltStack: minions, jobs, git states and pillars, database pillar, OpenBao secrets" \
      org.opencontainers.image.source="https://github.com/prototux/Salt-Conductor" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONDUCTOR_DATA_DIR=/data

# git + ssh: working copies of the states/pillar repositories
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --gid 0 --create-home --home-dir /home/conductor conductor \
 && install -d -o 10001 -g 0 -m 0770 /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gunicorn.conf.py .
COPY salt_conductor ./salt_conductor

USER 10001
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"

CMD ["gunicorn", "-c", "gunicorn.conf.py", "salt_conductor:create_app()"]
