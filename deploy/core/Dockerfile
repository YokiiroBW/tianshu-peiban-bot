# Build only after the complete source queue has been integrated and CR19 is released.
# The release manifest records immutable digests for every resolved stage.
ARG PYTHON_IMAGE
ARG NODE_IMAGE
ARG UV_IMAGE
ARG PG_CLIENT_IMAGE
ARG VECTOR_IMAGE
ARG LOKI_IMAGE
ARG PROMETHEUS_IMAGE
ARG GRAFANA_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm install --global npm@11.6.2 && npm ci
COPY web/ ./
RUN npm run build
FROM ${PG_CLIENT_IMAGE} AS postgres
FROM ${VECTOR_IMAGE} AS vector
FROM ${LOKI_IMAGE} AS loki
FROM ${PROMETHEUS_IMAGE} AS prometheus
FROM ${GRAFANA_IMAGE} AS grafana
FROM ${PYTHON_IMAGE} AS music-tools
WORKDIR /build
# Fixed local tools change independently of the Python product and web sources.
COPY tools/music/ ./tools/music/
RUN python tools/music/build.py
FROM ${PYTHON_IMAGE} AS python-build
COPY --from=uv /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/tianshu
COPY pyproject.toml uv.lock ./
COPY packages/tianshu-plugin/pyproject.toml packages/tianshu-plugin/pyproject.toml
# Resolve the exact locked third-party environment before any product source.
# Keep its unchanged snapshot separate from the subsequently installed product.
RUN uv sync --frozen --no-dev --no-install-project --no-install-package tianshu-plugin \
    && cp -a .venv /opt/tianshu/dependency-venv
COPY packages/tianshu-plugin/ packages/tianshu-plugin/
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable
FROM ${PYTHON_IMAGE} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git ca-certificates curl fontconfig fonts-noto-cjk \
    libpq5 liblz4-1 libzstd1 libxml2 libssl3 libreadline8 zlib1g libldap-2.5-0 libgssapi-krb5-2 libicu72 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 tianshu && useradd -m -u 10001 -g 10001 tianshu
COPY --from=python-build /opt/tianshu/dependency-venv /opt/tianshu/.venv
ENV PATH=/opt/tianshu/.venv/bin:/usr/lib/postgresql/17/bin:/usr/share/grafana/bin:$PATH \
    HOME=/home/tianshu PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/tianshu/browsers \
    TIKTOKEN_CACHE_DIR=/opt/tianshu/tokenizers
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/tianshu/browsers \
    && python -c "import tiktoken; tiktoken.get_encoding('o200k_base')" \
    && chmod -R a+rX /opt/tianshu/tokenizers \
    && rm -rf /var/lib/apt/lists/*
COPY --from=postgres /usr/lib/postgresql/17/ /usr/lib/postgresql/17/
COPY --from=postgres /usr/lib/x86_64-linux-gnu/libpq.so.5* /usr/lib/x86_64-linux-gnu/
COPY --from=vector /usr/bin/vector /usr/local/bin/vector
COPY --from=loki /usr/bin/loki /usr/local/bin/loki
COPY --from=prometheus /bin/prometheus /usr/local/bin/prometheus
COPY --from=prometheus /bin/promtool /usr/local/bin/promtool
COPY --from=grafana /usr/share/grafana /usr/share/grafana
RUN mkdir -p /opt/tianshu/image-manifest \
    && dpkg-query -W > /opt/tianshu/image-manifest/os-packages.txt \
    && ffmpeg -version > /opt/tianshu/image-manifest/ffmpeg.txt \
    && git --version > /opt/tianshu/image-manifest/git.txt \
    && pg_dump --version > /opt/tianshu/image-manifest/postgres-client.txt \
    && vector --version > /opt/tianshu/image-manifest/vector.txt \
    && loki --version > /opt/tianshu/image-manifest/loki.txt 2>&1
COPY --from=music-tools /build/music-tools/tianshu-musicbridge /build/music-tools/tianshu-musicdecode /usr/local/bin/
COPY --from=music-tools /build/music-tools/corresponding-source.tar.gz /usr/share/tianshu-music-tools/corresponding-source.tar.gz
# Source changes only replace product/static files and the small identity inventory.
COPY --from=python-build /opt/tianshu/.venv /opt/tianshu/.venv
COPY --from=web /build/web/dist /opt/tianshu/web/dist
COPY deploy/core /opt/tianshu/deploy/core
ARG TIANSHU_SOURCE_SHA
LABEL org.opencontainers.image.revision=${TIANSHU_SOURCE_SHA}
RUN python /opt/tianshu/deploy/core/image_inventory.py --source-sha "$TIANSHU_SOURCE_SHA"
WORKDIR /opt/tianshu
EXPOSE 18446 18443 18765
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python","/opt/tianshu/deploy/core/health.py","--config","/config/settings.json"]
ENTRYPOINT ["python","-m","tianshu"]
CMD ["serve","--config","/config/settings.json"]
