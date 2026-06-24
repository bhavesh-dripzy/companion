FROM public.ecr.aws/lambda/python:3.12

RUN microdnf install -y \
    gzip \
    shadow-utils \
    tar \
    xz \
    make \
    gcc \
    gcc-c++ && \
    curl -fsSL https://nodejs.org/dist/v20.19.0/node-v20.19.0-linux-x64.tar.xz \
    | tar -xJ -C /opt && \
    ln -s /opt/node-v20.19.0-linux-x64/bin/node /usr/local/bin/node && \
    ln -s /opt/node-v20.19.0-linux-x64/bin/npm /usr/local/bin/npm && \
    microdnf clean all

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt package.json package-lock.json ./
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}" && \
    npm ci --omit=dev

# Copy frontend source and build it
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend/ ./frontend/
RUN cd frontend && \
  echo "" > .env && \
  npm run build && rm -rf node_modules .env

COPY companion_api ./companion_api
# The clean pipeline package — MUST be bundled or companion_api.core_shadow's
# `import core...` fails at runtime (ModuleNotFoundError: No module named 'core').
COPY core ./core

# AWS Lambda Web Adapter — runs the app as a real ASGI server (uvicorn) inside
# Lambda, exactly like local, instead of Mangum. Combined with a RESPONSE_STREAM
# Function URL this lets SSE frames flush incrementally (true live streaming);
# it also transparently serves ordinary API Gateway invokes (e.g. ondc.*).
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

ENV PYTHONUNBUFFERED=1 \
    NODE_BINARY=/usr/local/bin/node \
    PYTHONPATH=${LAMBDA_TASK_ROOT} \
    PORT=8000 \
    AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_READINESS_CHECK_PATH=/api/

# Clear the base image's Lambda RIC entrypoint and run uvicorn as the main
# process; the LWA extension bridges the Lambda Runtime API to it. One worker —
# Lambda scales by concurrent execution environments, not in-process workers.
ENTRYPOINT []
CMD ["python", "-m", "uvicorn", "companion_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
