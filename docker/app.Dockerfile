FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY --from=astral/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv
ENV UV_SYSTEM_CERTS=true UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/hmrec/.venv/bin:$PATH"
WORKDIR /opt/hmrec
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=build_ca,required=true \
    SSL_CERT_FILE=/run/secrets/build_ca uv sync --locked --extra ui --group build --no-dev --no-install-project
COPY src ./src
COPY configs ./configs
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=build_ca,required=true \
    SSL_CERT_FILE=/run/secrets/build_ca uv sync --locked --extra ui --group build --no-dev --no-editable --no-build-isolation
USER 10001:10001
CMD ["hmrec", "check", "--imports-only"]
