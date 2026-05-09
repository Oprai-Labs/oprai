# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app

COPY services/solana-service-ts/package*.json ./
RUN npm ci --ignore-scripts

COPY services/solana-service-ts/tsconfig.json ./
COPY services/solana-service-ts/src/ ./src/

RUN npm run build

# ─── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM node:20-slim

RUN groupadd -r oprai && useradd -r -g oprai oprai

WORKDIR /app

COPY services/solana-service-ts/package*.json ./
RUN npm ci --only=production --ignore-scripts

COPY --from=builder /app/dist ./dist

USER oprai

ENV PORT=3031
ENV NODE_ENV=production

EXPOSE 3031

CMD ["node", "dist/index.js"]
