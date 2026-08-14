# ── Build stage ─────────────────────────────────────────────────────────
FROM golang:1.26-alpine AS builder

RUN apk add --no-cache ca-certificates git

WORKDIR /build

# Cache dependency downloads
COPY go.mod go.sum ./
RUN go mod download

# Copy source and build
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -ldflags="-w -s" \
    -o /auth-service \
    ./cmd/auth-service

# ── Runtime stage ──────────────────────────────────────────────────────
FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata

# Non-root user for security
RUN adduser -D -u 1001 appuser
USER appuser

WORKDIR /app

COPY --from=builder /auth-service .

# HTTP and gRPC ports
EXPOSE 3010 50051

ENTRYPOINT ["./auth-service"]
