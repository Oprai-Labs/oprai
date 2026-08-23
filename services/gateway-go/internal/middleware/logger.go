package middleware

import (
	"log/slog"
	"net/http"
	"time"

	chimiddleware "github.com/go-chi/chi/v5/middleware"
)

// loggerRecorder wraps http.ResponseWriter to capture the status code.
type loggerRecorder struct {
	http.ResponseWriter
	statusCode int
}

func (lr *loggerRecorder) WriteHeader(code int) {
	lr.statusCode = code
	lr.ResponseWriter.WriteHeader(code)
}

// LoggerMiddleware logs structured information about each HTTP request.
//
// trustProxy must match the rate limiter's setting: behind Caddy the real client
// IP arrives in X-Forwarded-For, so with a hardcoded false the log printed
// Caddy's container IP for every request — useless for abuse investigation and
// misleading (it looked like all traffic shared one IP). Now the log shows the
// same client IP the rate limiter keys on.
func LoggerMiddleware(trustProxy bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			recorder := &loggerRecorder{ResponseWriter: w, statusCode: http.StatusOK}

			next.ServeHTTP(recorder, r)

			duration := time.Since(start)
			wallet := r.Header.Get("X-User-Wallet")
			if wallet == "" {
				wallet = "-"
			}

			requestID := chimiddleware.GetReqID(r.Context())

			slog.Info("request",
				"request_id", requestID,
				"method", r.Method,
				"path", r.URL.Path,
				"status", recorder.statusCode,
				"duration_ms", duration.Milliseconds(),
				"wallet", wallet,
				"ip", extractIP(r, trustProxy),
			)
		})
	}
}
