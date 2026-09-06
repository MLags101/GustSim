FROM node:24-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS web
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN pnpm run build

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libxt6 libxrender1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY constraints.txt ./
COPY backend ./backend
COPY examples ./examples
RUN pip install --no-cache-dir -c constraints.txt '.[cad]' && useradd -u 1000 -m gustsim && mkdir /data && chown gustsim /data
COPY --from=web /app/dist ./dist
ENV GUSTSIM_DATA=/data PYTHONUNBUFFERED=1
USER gustsim
CMD ["uvicorn", "gustsim.api:app", "--host", "0.0.0.0", "--port", "8000"]
