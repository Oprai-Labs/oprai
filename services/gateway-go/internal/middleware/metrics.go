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
	gatewayRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "gateway_http_requests_total",
			Help: "Total number of HTTP requests to the gateway",
		},
		[]string{"method", "route", "status"},
	)

	gatewayRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "gateway_http_request_duration_seconds",
			Help:    "HTTP request duration in seconds",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"method", "route"},
	)

	gatewayRequestsInFlight = promauto.NewGauge(
		prometheus.GaugeOpts{
			Name: "gateway_http_requests_in_flight",
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
		gatewayRequestsInFlight.Inc()

		recorder := &metricsRecorder{ResponseWriter: w, statusCode: http.StatusOK}
		next.ServeHTTP(recorder, r)

		gatewayRequestsInFlight.Dec()
		duration := time.Since(start).Seconds()

		route := chi.RouteContext(r.Context()).RoutePattern()
		if route == "" {
			route = r.URL.Path
		}

		gatewayRequestsTotal.WithLabelValues(r.Method, route, strconv.Itoa(recorder.statusCode)).Inc()
		gatewayRequestDuration.WithLabelValues(r.Method, route).Observe(duration)
	})
}

