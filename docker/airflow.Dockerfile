FROM hmrec-jobs:0.1.0
USER root
COPY docker/airflow/requirements.lock /tmp/airflow-requirements.lock
RUN uv venv /opt/airflow-env --python /usr/local/bin/python
RUN --mount=type=cache,target=/root/.cache/uv --mount=type=secret,id=build_ca,required=true \
    SSL_CERT_FILE=/run/secrets/build_ca uv pip sync --python /opt/airflow-env/bin/python --require-hashes /tmp/airflow-requirements.lock
COPY dags /opt/airflow/dags
COPY docker/airflow/entrypoint.py /opt/airflow/entrypoint.py
ENV AIRFLOW_HOME=/opt/airflow/state PATH="/opt/airflow-env/bin:/opt/hmrec/.venv/bin:$PATH"
USER 10001:10001
ENTRYPOINT ["/opt/airflow-env/bin/python", "/opt/airflow/entrypoint.py"]
CMD ["version"]
