FROM golang:1.22-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /admin-service ./cmd/admin-service

FROM alpine:3.20

RUN apk add --no-cache ca-certificates tzdata

RUN adduser -D -u 1001 appuser
USER appuser

COPY --from=builder /admin-service /admin-service

EXPOSE 3050 50055

ENTRYPOINT ["/admin-service"]
