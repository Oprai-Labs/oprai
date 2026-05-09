package audit

import (
	"context"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"

	chimiddleware "github.com/go-chi/chi/v5/middleware"

	"github.com/oprai/oprai/services/auth-service-go/internal/db"
)

// Logger is an async audit event writer backed by a buffered channel and a
// single background worker. All public methods are safe for concurrent use.
type Logger struct {
	queries *db.Queries
	queue   chan db.AuditEvent
	once    sync.Once
}

// New creates a Logger. Call StartWorker(ctx) before enqueuing events.
func New(queries *db.Queries) *Logger {
	return &Logger{
		queries: queries,
		queue:   make(chan db.AuditEvent, 512),
	}
}

// StartWorker starts the background drain goroutine. Safe to call multiple
// times; only the first call takes effect (sync.Once guard).
func (l *Logger) StartWorker(ctx context.Context) {
	l.once.Do(func() {
		go func() {
			for {
				select {
				case evt, ok := <-l.queue:
					if !ok {
						return
					}
					l.write(evt)
				case <-ctx.Done():
					// Drain remaining events before stopping.
					for {
						select {
						case evt := <-l.queue:
							l.write(evt)
						default:
							return
						}
					}
				}
			}
		}()
	})
}

// Log enqueues an audit event for asynchronous DB write. Non-blocking: if the
// queue is full the event is dropped with a warning rather than blocking the
// caller.
func (l *Logger) Log(evt db.AuditEvent) {
	select {
	case l.queue <- evt:
	default:
		slog.Warn("audit queue full — dropping event",
			"event_type", evt.EventType,
			"wallet", evt.WalletAddress,
		)
	}
}

// LogRequest enqueues an audit event enriched with HTTP request metadata.
func (l *Logger) LogRequest(r *http.Request, evt db.AuditEvent, trustProxy bool) {
	evt.IPAddress = ExtractIP(r, trustProxy)
	if evt.UserAgent == "" {
		evt.UserAgent = r.Header.Get("User-Agent")
		if evt.UserAgent == "" {
			evt.UserAgent = "unknown"
		}
	}
	evt.RequestMethod = r.Method
	evt.RequestPath = r.URL.Path
	evt.RequestID = chimiddleware.GetReqID(r.Context())
	l.Log(evt)
}

func (l *Logger) write(evt db.AuditEvent) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := l.queries.InsertAuditEvent(ctx, &evt); err != nil {
		slog.Error("audit: failed to write event",
			"event_type", evt.EventType,
			"wallet", evt.WalletAddress,
			"error", err,
		)
	}
}

// ExtractIP returns the client IP address from the request, respecting proxy
// trust settings. Mirrors the auth_handler.go extractIP implementation.
func ExtractIP(r *http.Request, trustProxy bool) string {
	if trustProxy {
		if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
			parts := strings.SplitN(xff, ",", 2)
			if ip := strings.TrimSpace(parts[0]); ip != "" {
				return ip
			}
		}
		if xri := r.Header.Get("X-Real-IP"); xri != "" {
			return strings.TrimSpace(xri)
		}
	}
	// Strip port from host:port form.
	addr := r.RemoteAddr
	if i := strings.LastIndex(addr, ":"); i > 0 && strings.Contains(addr, ".") {
		return addr[:i]
	}
	return addr
}
