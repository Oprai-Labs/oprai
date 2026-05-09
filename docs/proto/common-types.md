# proto/common/types.proto

Common type definitions. Generic messages used by all services.

## File Information
- **Package**: `oprai.common`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/commonpb`
- **Dependencies**: `google/protobuf/timestamp.proto`

---

## Enums

### SortOrder
Enum for sort direction.

| Value | Number | Description |
|-------|--------|-------------|
| `SORT_ORDER_UNSPECIFIED` | 0 | Unspecified (default) |
| `SORT_ORDER_ASC` | 1 | Ascending sort (A->Z, 1->9) |
| `SORT_ORDER_DESC` | 2 | Descending sort (Z->A, 9->1) |

**Usage locations:**
- Specify sort direction in listing endpoints
- `auth-service`: User list
- `chat-service`: Message history
- `solana-service`: Transaction history

---

## Messages

### Pagination
Pagination parameters.

| Field | Type | Description |
|-------|------|-------------|
| `page` | `int32` | Page number (1-based) |
| `limit` | `int32` | Items per page (max 100) |

**Example usage:**
```protobuf
// Page 2, 20 items per page
Pagination pagination = { page: 2, limit: 20 };
```

**Used in:**
- `auth/UserService.ListUsers`
- `chat/SessionService.ListSessions`
- `chat/MessageService.ListMessages`
- `solana/ActionService.ListActions`

---

### PaginatedResponse
Paginated response metadata.

| Field | Type | Description |
|-------|------|-------------|
| `page` | `int32` | Current page |
| `limit` | `int32` | Items per page |
| `total_count` | `int64` | Total item count |
| `total_pages` | `int32` | Total page count |

**Returning services:**
- All listing endpoints include this metadata

---

### DateRange
Date range filter.

| Field | Type | Description |
|-------|------|-------------|
| `from` | `google.protobuf.Timestamp` | Start date |
| `to` | `google.protobuf.Timestamp` | End date |

**Use cases:**
- Query message history
- Filter transaction history
- Analytics reports

**Example:**
```protobuf
// Last 7 days
DateRange range = {
  from: { seconds: 1704067200 },  // 2024-01-01
  to: { seconds: 1704672000 }     // 2024-01-08
};
```

---

### Error
Error detail message.

| Field | Type | Description |
|-------|------|-------------|
| `code` | `int32` | gRPC status code |
| `message` | `string` | Error message |
| `details` | `map<string, string>` | Additional details (key-value) |

**gRPC Status Codes:**
| Code | Meaning |
|------|---------|
| 0 | OK |
| 1 | CANCELLED |
| 2 | UNKNOWN |
| 3 | INVALID_ARGUMENT |
| 4 | DEADLINE_EXCEEDED |
| 5 | NOT_FOUND |
| 6 | ALREADY_EXISTS |
| 7 | PERMISSION_DENIED |
| 8 | RESOURCE_EXHAUSTED |
| 9 | FAILED_PRECONDITION |
| 10 | ABORTED |
| 11 | OUT_OF_RANGE |
| 12 | UNIMPLEMENTED |
| 13 | INTERNAL |
| 14 | UNAVAILABLE |
| 15 | DATA_LOSS |
| 16 | UNAUTHENTICATED |

---

### Empty
Empty message. Used instead of `google.protobuf.Empty`.

| Field | Type | Description |
|-------|------|-------------|
| - | - | No fields |

**Usage:**
- RPCs that take no parameters
- RPCs that have no return value
- Example: `rpc Logout(Empty) returns (Empty);`

---

## Generated Code

### Go
```bash
# Generation command
protoc --go_out=. --go_opt=paths=source_relative \
  proto/common/types.proto

# Generated file
services/*/proto/gen/go/commonpb/types.pb.go
```

### Python
```bash
# Generation command
python -m grpc_tools.protoc -I./proto \
  --python_out=services/chat-service-py/proto_gen \
  proto/common/types.proto

# Generated file
services/chat-service-py/proto_gen/common/types_pb2.py
```

### Rust
```rust
// Automatic generation via tonic-build in build.rs
// Generated during cargo build
```

---

## Relationship Diagram

```
                    ┌─────────────┐
                    │  types.proto │
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
    ┌─────────┐      ┌─────────┐      ┌─────────┐
    │  auth   │      │  chat   │      │ solana  │
    │ *.proto │      │ *.proto │      │ *.proto │
    └─────────┘      └─────────┘      └─────────┘
```
