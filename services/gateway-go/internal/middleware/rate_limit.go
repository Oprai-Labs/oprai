package middleware

import (
	"context"
	"encoding/json"
	"log/slog"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

// ipLimiter stores a rate limiter and the last time it was seen.
type ipLimiter struct {
	limiter  *rate.Limiter
	lastSeen time.Time
}

// rateLimiterStore manages per-IP rate limiters.
type rateLimiterStore struct {
	mu       sync.RWMutex
	limiters map[string]*ipLimiter
	rate     rate.Limit
	burst    int
}

func newRateLimiterStore(ctx context.Context, r rate.Limit, burst int) *rateLimiterStore {
	store := &rateLimiterStore{
		limiters: make(map[string]*ipLimiter),
		rate:     r,
		burst:    burst,
	}

	// Background cleanup of stale entries. The goroutine stops when ctx is done
	// so it does not leak after graceful shutdown.
	go func() {
		ticker := time.NewTicker(5 * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				store.mu.Lock()
				now := time.Now()
				for ip, lim := range store.limiters {
					if now.Sub(lim.lastSeen) > 10*time.Minute {
						delete(store.limiters, ip)
					}
				}
				store.mu.Unlock()
			}
		}
	}()

	return store
}

func (s *rateLimiterStore) getLimiter(ip string) *rate.Limiter {
	s.mu.Lock()
	defer s.mu.Unlock()

	if lim, ok := s.limiters[ip]; ok {
		lim.lastSeen = time.Now()
		return lim.limiter
	}

	limiter := rate.NewLimiter(s.rate, s.burst)
	s.limiters[ip] = &ipLimiter{
		limiter:  limiter,
		lastSeen: time.Now(),
	}
	return limiter
}

// GlobalRateLimit returns a middleware that applies a global rate limit of
// 100 requests/min per IP. trustProxy controls whether X-Forwarded-For /
// X-Real-IP headers are trusted to identify the client IP — only enable this
// when the gateway is known to be behind a trusted reverse proxy.
func GlobalRateLimit(store *rateLimiterStore, trustProxy bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ip := extractIP(r, trustProxy)
			if !store.getLimiter(ip).Allow() {
				slog.Warn("Global rate limit exceeded",
					"ip", ip,
					"path", r.URL.Path,
					"method", r.Method,
				)
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Retry-After", "60")
				w.WriteHeader(http.StatusTooManyRequests)
				if err := json.NewEncoder(w).Encode(map[string]string{
					"error": "Too many requests, please try again later",
				}); err != nil {
					slog.Error("failed to encode rate limit response", "error", err)
				}
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// AuthRateLimit returns a middleware that applies a stricter rate limit of
// 20 requests/min per IP for authentication endpoints.
func AuthRateLimit(store *rateLimiterStore, trustProxy bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ip := extractIP(r, trustProxy)
			if !store.getLimiter(ip).Allow() {
				slog.Warn("Auth rate limit exceeded",
					"ip", ip,
					"path", r.URL.Path,
				)
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Retry-After", "60")
				w.WriteHeader(http.StatusTooManyRequests)
				if err := json.NewEncoder(w).Encode(map[string]string{
					"error": "Too many authentication attempts",
				}); err != nil {
					slog.Error("failed to encode rate limit response", "error", err)
				}
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// NewGlobalRateLimiterStore creates the per-IP store for the global limiter.
// 600/min (10/s) with a 200 burst: the Portfolio page legitimately fans out
// 40-50+ reads in one load (per-mint getAsset metadata, per-token OHLCV,
// per-protocol position builds, balances, sessions, auth), and a couple of
// refreshes at the old 100/min-burst-30 tripped a 429 cascade that broke logos,
// transaction parsing AND re-auth. These are cheap cached reads; abuse is still
// bounded (a bot can't sustain 10/s), and auth stays separately throttled.
// Pass the application root context so the background cleanup goroutine stops on shutdown.
func NewGlobalRateLimiterStore(ctx context.Context) *rateLimiterStore {
	return newRateLimiterStore(ctx, rate.Limit(600.0/60.0), 200)
}

// NewAuthRateLimiterStore creates the per-IP store for the auth limiter.
// 40/min burst 12: /auth/session + /auth/nonce are hit on restore, wallet
// (re)connect, and the sidebar load; the old 20/min-burst-5 429'd during a
// reconnect and starved the very re-auth that would recover it. Still strict
// enough to blunt SIWS-nonce brute force.
//
// The rate is env-overridable (GATEWAY_AUTH_RATE_PER_MIN / _BURST) with the
// production numbers as the default. A test suite authenticating a fresh user
// per case runs at hundreds a minute and 429s on a limit sized for humans;
// raising it in dev beats every live test failing for a reason unrelated to
// what it checks.
func NewAuthRateLimiterStore(ctx context.Context) *rateLimiterStore {
	perMin := envInt("GATEWAY_AUTH_RATE_PER_MIN", 40)
	burst := envInt("GATEWAY_AUTH_RATE_BURST", 12)
	return newRateLimiterStore(ctx, rate.Limit(float64(perMin)/60.0), burst)
}

// envInt reads a positive integer from the environment, falling back when it
// is unset or unreadable — a malformed override must not disable the limiter.
func envInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return fallback
}

// NewWalletRateLimiterStore creates the per-wallet store for the wallet (200/min) limiter.
// Pass the application root context so the background cleanup goroutine stops on shutdown.
// 200/min (≈3.3/s) with burst 60 accommodates portfolio page loads, which fire 15–25
// parallel requests on mount (RPC, Helius, price feed, sessions, position monitor, etc.).
func NewWalletRateLimiterStore(ctx context.Context) *rateLimiterStore {
	return newRateLimiterStore(ctx, rate.Limit(200.0/60.0), 60)
}

// WalletRateLimit returns middleware that applies a per-wallet rate limit of
// 200 requests/min. It only applies when a wallet is present in the context
// (i.e. after JWTAuth has run). Unauthenticated requests are not rate-limited
// by this middleware (they are covered by GlobalRateLimit).
func WalletRateLimit(store *rateLimiterStore) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			wallet := GetWallet(r.Context())
			if wallet == "" {
				// Not authenticated — skip wallet rate limit.
				next.ServeHTTP(w, r)
				return
			}
			if !store.getLimiter(wallet).Allow() {
				slog.Warn("Wallet rate limit exceeded",
					"wallet", wallet,
					"path", r.URL.Path,
					"method", r.Method,
				)
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Retry-After", "60")
				w.WriteHeader(http.StatusTooManyRequests)
				if err := json.NewEncoder(w).Encode(map[string]string{
					"error": "Too many requests from this wallet, please try again later",
				}); err != nil {
					slog.Error("failed to encode rate limit response", "error", err)
				}
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

// extractIP returns the client IP address from the request.
//
// When trustProxy is false (the default), the raw TCP connection address
// (r.RemoteAddr) is always used, making it impossible to spoof via headers.
//
// When trustProxy is true — only set this when the gateway runs behind a
// trusted reverse proxy — the leftmost non-empty value of X-Forwarded-For
// is used, falling back to X-Real-IP and then RemoteAddr.
func extractIP(r *http.Request, trustProxy bool) string {
	if trustProxy {
		if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
			// X-Forwarded-For may be a comma-separated list; take the first entry
			// (the original client IP as appended by the outermost proxy).
			parts := strings.SplitN(xff, ",", 2)
			if ip := strings.TrimSpace(parts[0]); ip != "" {
				return ip
			}
		}
		if xri := r.Header.Get("X-Real-IP"); xri != "" {
			return strings.TrimSpace(xri)
		}
	}

	// Fall back to the actual TCP connection address. Strip the port.
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}
