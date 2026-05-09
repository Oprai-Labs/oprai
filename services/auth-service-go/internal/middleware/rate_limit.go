package middleware

import (
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

// ipBucket tracks the number of requests from a single IP within a fixed window.
type ipBucket struct {
	count     int
	windowEnd time.Time
}

// IPRateLimiter is a simple fixed-window, in-memory per-IP rate limiter.
// It is designed for low-volume auth endpoints where a Redis dependency is
// undesirable; the in-process map is sufficient for single-instance deployments.
type IPRateLimiter struct {
	mu       sync.Mutex
	buckets  map[string]*ipBucket
	limit    int
	window   time.Duration
	stopOnce sync.Once
	stopCh   chan struct{}
}

// NewIPRateLimiter returns a rate limiter that allows at most limit requests
// per IP per window duration.
func NewIPRateLimiter(limit int, window time.Duration) *IPRateLimiter {
	rl := &IPRateLimiter{
		buckets: make(map[string]*ipBucket),
		limit:   limit,
		window:  window,
		stopCh:  make(chan struct{}),
	}
	// Background goroutine sweeps expired buckets every window to prevent unbounded growth.
	go rl.sweep()
	return rl
}

// Allow returns true if the request from ip is within the limit, false if it
// should be rejected. ip must be a raw IP string (no port).
func (rl *IPRateLimiter) Allow(ip string) bool {
	now := time.Now()
	rl.mu.Lock()
	defer rl.mu.Unlock()

	b, ok := rl.buckets[ip]
	if !ok || now.After(b.windowEnd) {
		rl.buckets[ip] = &ipBucket{count: 1, windowEnd: now.Add(rl.window)}
		return true
	}
	b.count++
	return b.count <= rl.limit
}

// Stop releases background goroutine resources.
func (rl *IPRateLimiter) Stop() {
	rl.stopOnce.Do(func() { close(rl.stopCh) })
}

func (rl *IPRateLimiter) sweep() {
	ticker := time.NewTicker(rl.window)
	defer ticker.Stop()
	for {
		select {
		case <-rl.stopCh:
			return
		case now := <-ticker.C:
			rl.mu.Lock()
			for ip, b := range rl.buckets {
				if now.After(b.windowEnd) {
					delete(rl.buckets, ip)
				}
			}
			rl.mu.Unlock()
		}
	}
}

// RateLimit returns a middleware that enforces the given IPRateLimiter.
// On rejection it writes HTTP 429 with a Retry-After header equal to the
// remaining window duration.
func RateLimit(rl *IPRateLimiter, trustProxy bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ip := clientIP(r, trustProxy)
			if !rl.Allow(ip) {
				w.Header().Set("Retry-After", "900") // 15 minutes
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusTooManyRequests)
				w.Write([]byte(`{"error":"too many requests","retry_after":900}`))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// clientIP extracts the client IP, honouring X-Forwarded-For only when trustProxy is true.
func clientIP(r *http.Request, trustProxy bool) string {
	if trustProxy {
		if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
			if ip := strings.TrimSpace(strings.SplitN(xff, ",", 2)[0]); ip != "" {
				return ip
			}
		}
		if xri := strings.TrimSpace(r.Header.Get("X-Real-IP")); xri != "" {
			return xri
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}
