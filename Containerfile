FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY --chown=65532:65532 src/ /app/src/

USER 65532:65532
EXPOSE 8787

ENTRYPOINT ["python", "-m", "opencode_go_proxy"]
CMD ["--bind", "0.0.0.0", "--port", "8787"]
