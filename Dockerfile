# Tempo-server: CPU only, no GPU needed.
#   docker build -t tempo-server .
#   docker run -it --rm -v tempo-data:/data tempo-server setup      # add your free keys
#   docker run -d -p 8000:8000 -v tempo-data:/data tempo-server     # http://localhost:8000
# Keys stored by `setup` stay encrypted in the /data volume; never bake a key into the image.

# Any Docker Hub mirror works too, e.g. --build-arg BASE=mirror.gcr.io/library/python:3.12-slim
ARG BASE=python:3.12-slim

FROM ${BASE} AS build
WORKDIR /src
RUN pip install --no-cache-dir "build>=1.2"
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY tempo ./tempo
RUN python -m build --wheel --outdir /dist

FROM ${BASE}
LABEL org.opencontainers.image.title="tempo-server" \
      org.opencontainers.image.description="Open models plus the system that runs and trains them: routes each question to the best free or open model and checks the answer, on CPU." \
      org.opencontainers.image.licenses="Apache-2.0"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TEMPO_DATA_DIR=/data \
    TEMPO_EMBEDDINGS=off
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl \
    && useradd --create-home --uid 10001 tempo \
    && mkdir -p /data && chown tempo:tempo /data
USER tempo
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
ENTRYPOINT ["tempo-server"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
