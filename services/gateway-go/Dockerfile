FROM golang:1.24-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /gateway ./cmd/gateway

FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata

RUN adduser -D -u 1001 appuser
USER appuser

COPY --from=builder /gateway /gateway

EXPOSE 3001

ENTRYPOINT ["/gateway"]
