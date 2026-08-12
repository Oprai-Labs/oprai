package middleware

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"sync"

	"github.com/go-chi/chi/v5"

	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// auditQueue is a buffered channel that decouples audit log writes from
// request handling. A single background worker drains it sequentially,
// preventing goroutine storms under high load while ensuring ordering.
var (
	auditQueue chan auditJob
	auditOnce  sync.Once
)

type auditJob struct {
	queries       *db.Queries
	adminUsername string
	action        string
	targetType    string
	targetID      string
	details       map[string]interface{}
	ipAddress     string
}

// StartAuditWorker starts the background goroutine that drains auditQueue.
// Call once at service startup, before handling any requests.
func StartAuditWorker(ctx context.Context) {
	auditOnce.Do(func() {
		auditQueue = make(chan auditJob, 512)
		go func() {
			for {
				select {
				case job, ok := <-auditQueue:
					if !ok {
						return
					}
					writeAuditLog(job)
				case <-ctx.Done():
					// Drain remaining jobs before exit.
					for {
						select {
						case job := <-auditQueue:
							writeAuditLog(job)
						default:
							return
						}
					}
				}
			}
		}()
	})
}

func writeAuditLog(job auditJob) {
	ctx := context.Background()
	adminID, err := job.queries.GetAdminIDByUsername(ctx, job.adminUsername)
	if err != nil {
		slog.Error("audit log: failed to find admin", "username", job.adminUsername, "error", err)
		return
	}
	if err := job.queries.CreateAuditLog(ctx, db.AuditLogEntry{
		AdminID:       adminID,
		AdminUsername: job.adminUsername,
		Action:        job.action,
		TargetType:    job.targetType,
		TargetID:      job.targetID,
		Details:       job.details,
		IPAddress:     job.ipAddress,
	}); err != nil {
		slog.Error("audit log: failed to create entry", "error", err)
	}
}

// statusRecorder wraps http.ResponseWriter to capture the response status code.
type statusRecorder struct {
	http.ResponseWriter
	statusCode int
}

func (sr *statusRecorder) WriteHeader(code int) {
	sr.statusCode = code
	sr.ResponseWriter.WriteHeader(code)
}

// AuditAction returns a middleware that logs admin actions to the audit log.
// It wraps a single handler (not a chain), capturing the response status and
// enqueuing an audit job on success (2xx). The actual DB write happens
// asynchronously in the background worker started by StartAuditWorker.
func AuditAction(queries *db.Queries, action string) func(http.HandlerFunc) http.HandlerFunc {
	return func(next http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			recorder := &statusRecorder{ResponseWriter: w, statusCode: http.StatusOK}

			next.ServeHTTP(recorder, r)

			// Only log on success (2xx).
			if recorder.statusCode >= 200 && recorder.statusCode < 300 {
				adminUsername := GetAdminUsername(r.Context())
				ip := ClientIP(r)
				targetType, targetID := extractTarget(r)
				details := extractAuditDetails(r, action)

				job := auditJob{
					queries:       queries,
					adminUsername: adminUsername,
					action:        action,
					targetType:    targetType,
					targetID:      targetID,
					details:       details,
					ipAddress:     ip,
				}

				// Non-blocking enqueue; drop if queue is full (overflow protection).
				select {
				case auditQueue <- job:
				default:
					slog.Warn("audit queue full — dropping audit log entry", "action", action)
				}
			}
		}
	}
}

// ClientIP returns the real client IP. The admin service is reachable only
// through the Caddy reverse proxy (it is bound to 127.0.0.1, and its routes are
// additionally VPN-restricted), and Caddy overwrites X-Real-IP / X-Forwarded-For
// with the real TCP peer. So the proxy-set header is the true client address and
// cannot be forged by an external caller — one can't reach the service except
// through Caddy. Falls back to the socket address for direct/local calls.
func ClientIP(r *http.Request) string {
	if xrip := strings.TrimSpace(r.Header.Get("X-Real-IP")); xrip != "" {
		return xrip
	}
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		parts := strings.SplitN(xff, ",", 2)
		return strings.TrimSpace(parts[0])
	}
	if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return host
	}
	return strings.TrimSpace(r.RemoteAddr)
}

func extractTarget(r *http.Request) (string, string) {
	if id := chi.URLParam(r, "id"); id != "" {
		return "", id
	}
	return "", ""
}

func extractAuditDetails(r *http.Request, action string) map[string]interface{} {
	details := make(map[string]interface{})

	if r.Method == "POST" || r.Method == "PUT" || r.Method == "PATCH" {
		body, err := io.ReadAll(r.Body)
		if err == nil && len(body) > 0 {
			r.Body = io.NopCloser(bytes.NewBuffer(body))
			var bodyMap map[string]interface{}
			if json.Unmarshal(body, &bodyMap) == nil {
				delete(bodyMap, "password")
				delete(bodyMap, "password_hash")
				delete(bodyMap, "token")
				delete(bodyMap, "secret")
				for k, v := range bodyMap {
					details[k] = v
				}
			}
		}
	}

	if len(details) == 0 {
		return nil
	}
	return details
}
