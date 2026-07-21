# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app

COPY services/solana-service-ts/package*.json ./
# npm install (not ci) so the linux builder resolves its own platform-specific
# optional native deps (utf-8-validate/bufferutil) instead of failing on a
# macOS-generated lock that omits them. --ignore-scripts skips native compiles.
RUN npm install --ignore-scripts --no-audit --no-fund

COPY services/solana-service-ts/tsconfig.json ./
COPY services/solana-service-ts/src/ ./src/

RUN npm run build

# ─── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM node:20-slim

RUN groupadd -r oprai && useradd -r -g oprai oprai

WORKDIR /app

COPY services/solana-service-ts/package*.json ./
RUN npm install --omit=dev --ignore-scripts --no-audit --no-fund

COPY --from=builder /app/dist ./dist

USER oprai

ENV PORT=3031
ENV NODE_ENV=production

EXPOSE 3031

CMD ["node", "dist/index.js"]
