FROM node:22-alpine AS frontend
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim AS runtime

WORKDIR /app
COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY src ./src
COPY --from=frontend /web/dist ./web/dist
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "smms.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM nginx:1.27-alpine AS web
COPY --from=frontend /web/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
