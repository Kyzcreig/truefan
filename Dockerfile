FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/truefan

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ipmitool \
        lm-sensors \
        procps \
        smartmontools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/truefan
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt

RUN groupadd --gid 10001 truefan \
    && useradd --uid 10001 --gid truefan --create-home --shell /usr/sbin/nologin truefan \
    && mkdir -p /data /opt/truefan \
    && chown -R truefan:truefan /data /opt/truefan

COPY --chown=truefan:truefan app ./app
COPY --chown=truefan:truefan truefan_control ./truefan_control
COPY --chown=truefan:truefan entrypoint.sh healthcheck.py ./
RUN chmod 0555 /opt/truefan/entrypoint.sh /opt/truefan/healthcheck.py

EXPOSE 5002 5088
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python", "/opt/truefan/healthcheck.py"]

USER truefan
ENTRYPOINT ["/opt/truefan/entrypoint.sh"]
