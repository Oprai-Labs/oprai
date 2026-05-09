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
func LoggerMiddleware(next http.Handler) http.Handler {
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
			"ip", extractIP(r, false),
		)
	})
}
