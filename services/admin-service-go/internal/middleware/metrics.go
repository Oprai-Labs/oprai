package middleware

import (
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	httpRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "admin_http_requests_total",
			Help: "Total number of HTTP requests",
		},
		[]string{"method", "route", "status"},
	)

	httpRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "admin_http_request_duration_seconds",
			Help:    "HTTP request duration in seconds",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"method", "route"},
	)

	httpRequestsInFlight = promauto.NewGauge(
		prometheus.GaugeOpts{
			Name: "admin_http_requests_in_flight",
			Help: "Number of HTTP requests currently being processed",
		},
	)
)

// metricsRecorder wraps http.ResponseWriter to capture the status code.
type metricsRecorder struct {
	http.ResponseWriter
	statusCode int
}

func (mr *metricsRecorder) WriteHeader(code int) {
	mr.statusCode = code
	mr.ResponseWriter.WriteHeader(code)
}

// MetricsMiddleware records Prometheus metrics for each HTTP request.
func MetricsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		httpRequestsInFlight.Inc()

		recorder := &metricsRecorder{ResponseWriter: w, statusCode: http.StatusOK}
		next.ServeHTTP(recorder, r)

		httpRequestsInFlight.Dec()
		duration := time.Since(start).Seconds()

		// Use the chi route pattern if available, otherwise use the path
		route := chi.RouteContext(r.Context()).RoutePattern()
		if route == "" {
			route = r.URL.Path
		}

		httpRequestsTotal.WithLabelValues(r.Method, route, strconv.Itoa(recorder.statusCode)).Inc()
		httpRequestDuration.WithLabelValues(r.Method, route).Observe(duration)
	})
}
