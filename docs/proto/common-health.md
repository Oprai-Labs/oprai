# proto/common/health.proto

Health check service. Used to monitor the health status of all microservices.

## File Information
- **Package**: `oprai.common`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/commonpb`
- **Dependencies**: `google/protobuf/timestamp.proto`

---

## Service: HealthService

### rpc Check
Service health check. Compliant with gRPC health checking standard.

```protobuf
rpc Check(HealthCheckRequest) returns (HealthCheckResponse);
```

**Called by:**
- Gateway `/health` endpoint -> fans out to all services
- Kubernetes liveness/readiness probes
- Prometheus health scraper
- Docker health checks

---

## Messages

### HealthCheckRequest

| Field | Type | Description |
|-------|------|-------------|
| `service` | `string` | Service name to check (optional) |

**Behavior:**
- `service` empty -> check the server itself
- `service` filled -> check the specified downstream service

---

### HealthCheckResponse

| Field | Type | Description |
|-------|------|-------------|
| `status` | `ServingStatus` | Overall service status |
| `version` | `string` | Service version (git commit or semver) |
| `uptime_seconds` | `double` | Uptime in seconds |
| `started_at` | `google.protobuf.Timestamp` | Start time |
| `services` | `repeated ServiceStatus` | Downstream service statuses |

---

### ServingStatus (Enum)

| Value | Number | Description |
|-------|--------|-------------|
| `SERVING_STATUS_UNSPECIFIED` | 0 | Unknown |
| `SERVING_STATUS_SERVING` | 1 | Active and running |
| `SERVING_STATUS_NOT_SERVING` | 2 | Not running |
| `SERVING_STATUS_STARTING` | 3 | Starting up |

---

### ServiceStatus

Status of downstream dependencies (DB, Redis, Qdrant, etc.)

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Service name (e.g., "postgres", "redis", "qdrant") |
| `status` | `ServingStatus` | Service status |
| `message` | `string` | Optional status message |
| `latency_ms` | `double` | Response time in milliseconds |

---

## Usage Examples

### Go Implementation
```go
// services/auth-service-go/internal/grpc/health.go
type HealthServer struct {
    commonpb.UnimplementedHealthServiceServer
    startTime time.Time
    version   string
}

func (s *HealthServer) Check(ctx context.Context, req *commonpb.HealthCheckRequest) (*commonpb.HealthCheckResponse, error) {
    resp := &commonpb.HealthCheckResponse{
        Status:        commonpb.ServingStatus_SERVING_STATUS_SERVING,
        Version:       s.version,
        UptimeSeconds: time.Since(s.startTime).Seconds(),
        StartedAt:     timestamppb.New(s.startTime),
        Services:      s.checkDependencies(ctx),
    }
    return resp, nil
}

func (s *HealthServer) checkDependencies(ctx context.Context) []*commonpb.ServiceStatus {
    return []*commonpb.ServiceStatus{
        s.checkPostgres(ctx),
        s.checkRedis(ctx),
    }
}
```

### Python Implementation
```python
# services/chat-service-py/app/grpc/health.py
class HealthServicer(common_pb2_grpc.HealthServiceServicer):
    def __init__(self):
        self.start_time = datetime.utcnow()
        self.version = os.getenv("APP_VERSION", "dev")

    async def Check(self, request, context):
        return common_pb2.HealthCheckResponse(
            status=common_pb2.SERVING_STATUS_SERVING,
            version=self.version,
            uptime_seconds=(datetime.utcnow() - self.start_time).total_seconds(),
            started_at=Timestamp(seconds=int(self.start_time.timestamp())),
            services=await self._check_deps(),
        )
```

---

## Gateway Health Aggregation

The Gateway `/health` endpoint collects health from all services:

```
GET /health
    |
    |--- gRPC -> auth-service:50051/Check
    |--- gRPC -> chat-service:50052/Check
    |--- gRPC -> solana-service:50053/Check
    `-- gRPC -> memory-service:50054/Check
                |
                v
        Aggregated JSON Response
```

**Example Response:**
```json
{
  "status": "SERVING",
  "version": "1.0.0",
  "uptime_seconds": 3600,
  "started_at": "2024-01-15T10:00:00Z",
  "services": [
    {
      "name": "auth-service",
      "status": "SERVING",
      "latency_ms": 5.2
    },
    {
      "name": "chat-service",
      "status": "SERVING",
      "latency_ms": 8.1
    },
    {
      "name": "solana-service",
      "status": "SERVING",
      "latency_ms": 12.3
    },
    {
      "name": "memory-service",
      "status": "SERVING",
      "latency_ms": 3.4
    }
  ]
}
```

---

## Kubernetes Integration

```yaml
livenessProbe:
  exec:
    command: ["grpc_health_probe", "-addr=:50051"]
  initialDelaySeconds: 10
  periodSeconds: 30

readinessProbe:
  exec:
    command: ["grpc_health_probe", "-addr=:50051"]
  initialDelaySeconds: 5
  periodSeconds: 10
```

---

## Service Dependencies

| Service | Downstream Dependencies |
|--------|------------------------|
| auth-service | PostgreSQL, Redis |
| chat-service | PostgreSQL, Qdrant, OpenAI API |
| memory-service | Qdrant, OpenAI API |
| solana-service | PostgreSQL, Solana RPC |
| admin-service | PostgreSQL |
| gateway | All services (gRPC) |
