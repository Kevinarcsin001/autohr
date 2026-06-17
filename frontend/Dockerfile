# syntax=docker/dockerfile:1.6
FROM node:20-alpine AS base

ENV PNPM_HOME=/usr/local/share/pnpm \
    PATH=$PNPM_HOME:$PATH \
    CI=1 \
    # npm 国内镜像加速
    npm_config_registry=https://registry.npmmirror.com

RUN corepack enable && corepack prepare pnpm@8.15.4 --activate

WORKDIR /app

# ---------------- development ----------------
FROM base AS development
EXPOSE 3000
COPY package.json pnpm-lock.yaml* ./
# npm 镜像加速后通常 30s 内完成
RUN pnpm config set registry https://registry.npmmirror.com \
    && (pnpm install --frozen-lockfile || pnpm install)
COPY . .
CMD ["pnpm", "dev"]

# ---------------- builder (production) ----------------
FROM base AS builder
COPY package.json pnpm-lock.yaml* ./
RUN pnpm config set registry https://registry.npmmirror.com \
    && (pnpm install --frozen-lockfile || pnpm install)
COPY . .
RUN pnpm build

# ---------------- production ----------------
FROM base AS production
ENV NODE_ENV=production
EXPOSE 3000
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.mjs ./next.config.mjs
CMD ["pnpm", "start"]
