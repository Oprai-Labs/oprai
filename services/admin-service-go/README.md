# Admin Service (Go)

Admin panel backend with cross-schema database access for platform management.

## Responsibilities

- Separate admin authentication (username/password + bcrypt)
- Cross-schema SQL queries (auth, chat, solana, memory schemas)
- User management (list, view, suspend)
- Platform analytics (aggregated stats)
- Audit logging for admin actions
- Does NOT go through the gateway — direct access

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/login` | Admin login (username/password) |
| GET | `/admin/users` | List all users (cross-schema) |
| GET | `/admin/users/:id` | Get user details |
| PUT | `/admin/users/:id/status` | Suspend/activate user |
| GET | `/admin/stats` | Platform statistics |
| GET | `/admin/analytics` | Detailed analytics |
| GET | `/admin/audit-log` | Admin action audit trail |

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | HTTP port (default: 3050) |
| `GRPC_PORT` | No | gRPC port (default: 50055) |
| `OPRAI_ADMIN_JWT_SECRET` | Yes | Admin JWT signing secret |
| `ADMIN_CORS_ORIGIN` | No | CORS origin (default: `http://localhost:3200`) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |

## Database Schema

- **Schema**: `admin_schema`
- **Tables**: `admin_users`, `admin_audit_log`
- **Default admin**: `admin` / `admin123` (change in production)

## Run

```bash
# Dev (native)
cd services/admin-service-go && go run ./cmd/admin-service

# Docker
docker compose -f infra/docker-compose.yml up admin-service-go

# Build binary
cd services/admin-service-go && go build -o bin/admin-service ./cmd/admin-service
```

## Project Structure

```
admin-service-go/
├── cmd/
│   └── admin-service/     Entry point
├── internal/
│   ├── config/           Configuration loading
│   ├── handlers/         HTTP handlers
│   ├── models/           Data models
│   ├── repository/       Cross-schema database queries
│   ├── service/          Business logic
│   └── middleware/       Admin auth middleware
├── sql/                  Migration files
├── Dockerfile
├── go.mod
└── go.sum
```

## Security Notes

- Admin service has direct cross-schema database access — do NOT expose publicly
- Uses separate JWT secret from main auth flow
- All admin actions are logged in `admin_audit_log`
- Default credentials must be changed before production deployment
