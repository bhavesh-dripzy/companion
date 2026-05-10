FROM public.ecr.aws/lambda/python:3.12

RUN microdnf install -y \
    curl \
    gzip \
    shadow-utils \
    tar \
    xz && \
    curl -fsSL https://nodejs.org/dist/v20.19.0/node-v20.19.0-linux-x64.tar.xz \
    | tar -xJ -C /opt && \
    ln -s /opt/node-v20.19.0-linux-x64/bin/node /usr/local/bin/node && \
    ln -s /opt/node-v20.19.0-linux-x64/bin/npm /usr/local/bin/npm && \
    microdnf clean all

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt package.json package-lock.json ./
RUN pip install --no-cache-dir -r requirements.txt --target "${LAMBDA_TASK_ROOT}" && \
    npm ci --omit=dev

COPY app/__init__.py ./app/__init__.py
COPY app/services ./app/services
COPY companion_api ./companion_api

ENV PYTHONUNBUFFERED=1 \
    NODE_BINARY=/usr/local/bin/node

CMD ["companion_api.main.handler"]
