FROM golang:1.25-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /gateway ./cmd/gateway

FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata

RUN adduser -D -u 1001 appuser

# Uploaded token images and metadata JSON live here.
#
# There was no such directory and no WORKDIR, so the working directory was `/`,
# which appuser cannot write. Every POST /upload/image in production failed
# with "mkdir uploads: permission denied" — and because the launch flow treated
# that as a soft failure, a token went out with a browser blob: URL as its
# permanent on-chain metadata address.
#
# Mount a volume over /data in production: these files are referenced by
# immutable on-chain URIs, so losing them breaks tokens that already exist.
RUN mkdir -p /data/uploads && chown -R appuser:appuser /data
ENV UPLOAD_DIR=/data/uploads

USER appuser
WORKDIR /data

COPY --from=builder /gateway /gateway

EXPOSE 3001

ENTRYPOINT ["/gateway"]
