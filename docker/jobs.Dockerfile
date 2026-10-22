FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY --from=astral/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv
ENV UV_SYSTEM_CERTS=true UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/hmrec/.venv/bin:$PATH"
WORKDIR /opt/hmrec
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1=12.2.0-14+deb12u1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=build_ca,required=true \
    SSL_CERT_FILE=/run/secrets/build_ca uv sync --locked --extra tracking --extra ui --group build --group test --no-install-project
COPY src ./src
COPY configs ./configs
COPY migrations ./migrations
COPY tests ./tests
COPY scripts/handoff.py scripts/quality_handoff.py scripts/quality_summary.py scripts/release_handoff.py ./scripts/
COPY scripts/serving_handoff.py scripts/events_handoff.py ./scripts/
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=build_ca,required=true \
    SSL_CERT_FILE=/run/secrets/build_ca uv sync --locked --extra tracking --extra ui --group build --group test --no-editable --no-build-isolation
USER 10001:10001
CMD ["hmrec", "check", "--imports-only"]
