FROM node:24.15.0-alpine@sha256:d1b3b4da11eefd5941e7f0b9cf17783fc99d9c6fc34884a665f40a06dbdfc94f AS frontend
WORKDIR /web
COPY web/package*.json ./
COPY web/.npmrc ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements/runtime.lock ./requirements/runtime.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements/runtime.lock
COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY configs ./configs
COPY src ./src
COPY --from=frontend /web/dist ./web/dist
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation . && python -m pip check

EXPOSE 8000
CMD ["uvicorn", "smms.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM nginx:stable-alpine@sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311 AS web
COPY --from=frontend /web/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
