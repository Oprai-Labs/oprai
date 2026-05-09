# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM rust:1.89-slim AS builder

RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    protobuf-compiler \
    libpq-dev \
    perl \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Force use of system OpenSSL instead of vendored build
ENV OPENSSL_NO_VENDOR=1
ENV PKG_CONFIG_ALLOW_CROSS=1

WORKDIR /app

# Copy proto files first (for tonic-build).
COPY proto/ /app/proto/

# Copy shared assets referenced via include_str! at compile time.
COPY shared/ /app/shared/

# Copy Cargo files for dependency caching.
COPY services/solana-service-rs/Cargo.toml services/solana-service-rs/Cargo.lock* /app/services/solana-service-rs/
COPY services/solana-service-rs/build.rs /app/services/solana-service-rs/

# Migrations are include_str!'d into the binary at compile time.
COPY services/solana-service-rs/migrations/ /app/services/solana-service-rs/migrations/

# Create a dummy main.rs for dependency pre-build.
RUN mkdir -p /app/services/solana-service-rs/src && \
    echo 'fn main() {}' > /app/services/solana-service-rs/src/main.rs

WORKDIR /app/services/solana-service-rs
RUN cargo build --release 2>/dev/null || true

# Copy the real source.
COPY services/solana-service-rs/src/ /app/services/solana-service-rs/src/

# Touch main.rs so cargo rebuilds with real code.
RUN touch src/main.rs

RUN cargo build --release

# ─── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r oprai && useradd -r -g oprai oprai

COPY --from=builder /app/services/solana-service-rs/target/release/oprai-solana-service /usr/local/bin/oprai-solana-service

USER oprai

ENV PORT=3030
ENV GRPC_PORT=50053

EXPOSE 3030 50053

CMD ["oprai-solana-service"]
