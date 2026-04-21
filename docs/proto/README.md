# Proto Documentation

Detailed documentation for all gRPC proto files in the OPRAI project.

## File Index

| File | Topic | Service |
|------|-------|---------|
| [common-types.md](./common-types.md) | Shared type definitions | - |
| [common-health.md](./common-health.md) | Health check | All services |
| [auth-service.md](./auth-service.md) | SIWS authentication | auth-service-go |
| [auth-user.md](./auth-user.md) | User management | auth-service-go |
| [chat-session.md](./chat-session.md) | Chat sessions | chat-service-py |
| [chat-message.md](./chat-message.md) | Messaging (streaming) | chat-service-py |
| [solana-action.md](./solana-action.md) | Transaction building | solana-service-rs |
| [solana-quote.md](./solana-quote.md) | Swap quote | solana-service-rs |
| [solana-protocol.md](./solana-protocol.md) | Protocol/token metadata | solana-service-rs |
| [memory-service.md](./memory-service.md) | Vector memory | memory-service-py |
| [admin-service.md](./admin-service.md) | Admin panel | admin-service-go |
| [stream-service.md](./stream-service.md) | Real-time streaming | - |

## Code Generation

```bash
# All languages
make proto

# Single language
./scripts/build-protos.sh go
./scripts/build-protos.sh python
./scripts/build-protos.sh rust
```

### Requirements

- `protoc` (`brew install protobuf`)
- Go: `protoc-gen-go`, `protoc-gen-go-grpc`
- Python: `grpcio-tools`
- Rust: `tonic-build` (automatic via `build.rs`)

## Service Architecture

```
                        ┌─────────────────────────────────────────────────────┐
                        │                   GATEWAY (:3001)                    │
                        │        JWT Validation · Rate Limiting · CORS        │
                        └───────────────────────────┬─────────────────────────┘
                                                    │
        ┌───────────────────┬───────────────────────┼───────────────────────┬───────────────────┐
        │                   │                       │                       │                   │
        ▼                   ▼                       ▼                       ▼                   ▼
┌───────────────┐   ┌───────────────┐       ┌───────────────┐       ┌───────────────┐   ┌───────────────┐
│  Auth Service │   │ Chat Service  │       │ Solana Service│       │Memory Service │   │Admin Service  │
│   Go :3010    │   │  Py :3020     │       │  Rust :3030   │       │  Py :3040     │   │  Go :3050     │
│  gRPC :50051  │   │  gRPC :50052  │       │  gRPC :50053  │       │  gRPC :50054  │   │  gRPC :50055  │
└───────────────┘   └───────────────┘       └───────────────┘       └───────────────┘   └───────────────┘
        │                   │                       │                       │                   │
        ▼                   ▼                       ▼                       ▼                   ▼
   PostgreSQL          PostgreSQL              PostgreSQL              Qdrant             PostgreSQL
   auth_schema         chat_schema             solana_schema                              admin_schema
```

## Proto File Map

### common/
- `types.proto` → `commonpb` → Pagination, DateRange, Error, Empty
- `health.proto` → `commonpb` → HealthService

### auth/
- `auth.proto` → `authpb` → AuthService (SIWS)
- `user.proto` → `authpb` → UserService

### chat/
- `session.proto` → `chatpb` → ChatSessionService
- `message.proto` → `chatpb` → ChatMessageService (streaming)

### solana/
- `action.proto` → `solanapb` → SolanaActionService
- `quote.proto` → `solanapb` → SolanaQuoteService
- `protocol.proto` → `solanapb` → SolanaProtocolService

### memory/
- `consent.proto` → `memorypb` → MemoryConsentService
- `vector.proto` → `memorypb` → MemoryVectorService

### admin/
- `admin_auth.proto` → `adminpb` → AdminAuthService
- `analytics.proto` → `adminpb` → AdminAnalyticsService
- `audit.proto` → `adminpb` → AdminAuditService

### stream/
- `stream.proto` → `streampb` → StreamService (streaming)

## Dependency Graph

```
                    ┌─────────────┐
                    │   common/   │
                    │  types.proto│
                    │ health.proto│
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┬─────────────────┐
         │                 │                 │                 │
         ▼                 ▼                 ▼                 ▼
    ┌─────────┐      ┌─────────┐      ┌─────────┐      ┌─────────┐
    │  auth/  │      │  chat/  │      │ solana/ │      │ memory/ │
    │*.proto  │      │*.proto  │      │*.proto  │      │*.proto  │
    └─────────┘      └─────────┘      └─────────┘      └─────────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   admin/    │
                    │  *.proto    │
                    └─────────────┘
```

## Request/Response Patterns

### Pagination
```protobuf
// Request
message ListRequest {
    oprai.common.Pagination pagination = 1;
}

// Response
message ListResponse {
    repeated Item items = 1;
    oprai.common.PaginatedResponse pagination = 2;
}
```

### Oneof Identifier
```protobuf
message GetRequest {
    oneof identifier {
        string id = 1;
        string wallet_address = 2;
    }
}
```

### Streaming
```protobuf
// Server streaming
rpc Subscribe(Request) returns (stream Response);

// Bidirectional streaming
rpc ChatStream(stream Request) returns (stream Response);
```

## Generated Code Output

### Go
```bash
protoc --go_out=. --go_opt=paths=source_relative \
       --go-grpc_out=. --go-grpc_opt=paths=source_relative \
       proto/auth/auth.proto

# Output
# services/auth-service-go/proto/gen/go/authpb/auth.pb.go
# services/auth-service-go/proto/gen/go/authpb/auth_grpc.pb.go
```

### Python
```bash
python -m grpc_tools.protoc \
    -I./proto \
    --python_out=services/chat-service-py/proto_gen \
    --grpc_python_out=services/chat-service-py/proto_gen \
    proto/chat/message.proto

# Output
# services/chat-service-py/proto_gen/chat/message_pb2.py
# services/chat-service-py/proto_gen/chat/message_pb2_grpc.py
```

### Rust
```rust
// build.rs — runs automatically at cargo build time
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .build_server(true)
        .build_client(true)
        .compile(
            &["proto/solana/action.proto"],
            &["proto"]
        )?;
    Ok(())
}

// Output (in target/)
// solana.action.rs
```

## Common Commands

```bash
# Lint proto files
buf lint proto/

# Check for breaking changes
buf breaking proto/ --against '.git#branch=main'

# Generate single file (Go)
protoc --go_out=. --go-grpc_out=. proto/auth/auth.proto

# Generate all
make proto
```
