FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend AEROTWIN_HOST=0.0.0.0 PORT=7860
# non-root user (required by Hugging Face Spaces, good practice everywhere)
RUN useradd -m -u 1000 user
WORKDIR /app
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt
COPY --chown=user:user . .
RUN mkdir -p /app/data && chown -R user:user /app
USER user
EXPOSE 7860
# Trained models are already in /models; if they ever fail to load the server re-trains itself at start-up.
CMD ["python", "run.py"]
