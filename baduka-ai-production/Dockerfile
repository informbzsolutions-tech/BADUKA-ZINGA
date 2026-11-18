FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/baduka
RUN addgroup --system app && adduser --system --ingroup app app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod 755 ./entrypoint.sh && chown -R app:app /srv/baduka
USER app
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
