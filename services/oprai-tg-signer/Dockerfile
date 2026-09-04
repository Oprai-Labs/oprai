# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM rust:1.90-slim AS builder

RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    perl \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

ENV OPENSSL_NO_VENDOR=1

WORKDIR /app/signer

# Dependencies first, so a source change doesn't rebuild the world.
COPY services/oprai-tg-signer/Cargo.toml services/oprai-tg-signer/Cargo.lock* ./
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs && \
    cargo build --release 2>/dev/null || true

COPY services/oprai-tg-signer/src/ ./src/
# Touch so cargo rebuilds the real binary rather than reusing the dummy.
RUN touch src/main.rs && cargo build --release

# ─── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# The only process that ever touches key material runs unprivileged.
RUN useradd --system --create-home --shell /usr/sbin/nologin signer

COPY --from=builder /app/signer/target/release/oprai-tg-signer /usr/local/bin/

USER signer
EXPOSE 3060

# Fail-closed by design: without Vault the signing endpoints stay disabled, so
# health is the honest check that the process is up and answering.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:3060/health || exit 1

CMD ["oprai-tg-signer"]
