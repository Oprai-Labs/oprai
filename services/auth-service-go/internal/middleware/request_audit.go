package middleware

import (
	"net/http"
	"time"

	chimiddleware "github.com/go-chi/chi/v5/middleware"

	"github.com/oprai/oprai/services/auth-service-go/internal/audit"
	"github.com/oprai/oprai/services/auth-service-go/internal/db"
)

// RequestAudit returns a middleware that logs API errors (4xx/5xx) and slow
// requests (>1s) to the audit_trail table. Successful fast requests are not
// DB-logged to avoid drowning the table with health checks and normal traffic.
func RequestAudit(auditLogger *audit.Logger, trustProxy bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Skip health and metrics endpoints — they're noisy and low-value.
			path := r.URL.Path
			if path == "/health" || path == "/metrics" {
				next.ServeHTTP(w, r)
				return
			}

			start := time.Now()
			rec := &statusRecorder{ResponseWriter: w, statusCode: http.StatusOK}
			next.ServeHTTP(rec, r)
			elapsed := time.Since(start)

			status := rec.statusCode
			durationMs := float64(elapsed.Milliseconds())

			// Only emit an audit row for errors or genuinely slow responses.
			if status < 400 && elapsed < time.Second {
				return
			}

			wallet := WalletFromContext(r.Context())

			var eventType, severity string
			switch {
			case status >= 500:
				eventType = "system_error"
				severity = "critical"
			case status == 429:
				eventType = "rate_limit_exceeded"
				severity = "warning"
			case status == 401 || status == 403:
				eventType = "unauthorized_access"
				severity = "error"
			case status >= 400:
				eventType = "api_error"
				severity = "warning"
			default:
				eventType = "api_slow_request"
				severity = "info"
			}

			auditLogger.LogRequest(r, db.AuditEvent{
				EventType:      eventType,
				Severity:       severity,
				WalletAddress:  wallet,
				ResponseStatus: status,
				ResponseTimeMs: durationMs,
				RequestID:      chimiddleware.GetReqID(r.Context()),
			}, trustProxy)
		})
	}
}
