package middleware

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// TokenBlocklist tracks revoked JWT IDs (jti). On logout, the gateway adds
// the token's jti here so that copies of the Bearer token (e.g. cached in
// localStorage) are rejected immediately.
//
// Two-tier design:
//   - Redis (authoritative, shared across gateway instances, survives restart)
//   - in-memory cache in front of Redis to avoid a network round-trip on every
//     authenticated request
//
// The cache is bounded only by token expiry — old entries are evicted by the
// cleanup loop when their TTL elapses. If Redis is nil (dev without Redis),
// the blocklist degrades to in-memory only.
type TokenBlocklist struct {
	rdb       *redis.Client
	keyPrefix string

	mu      sync.RWMutex
	entries map[string]time.Time // jti → expiry
}

// NewTokenBlocklist creates a TokenBlocklist. If rdb is non-nil, Redis is the
// authoritative store; otherwise the blocklist is per-process only.
func NewTokenBlocklist(ctx context.Context, rdb *redis.Client) *TokenBlocklist {
	bl := &TokenBlocklist{
		rdb:       rdb,
		keyPrefix: "oprai:auth:revoked:",
		entries:   make(map[string]time.Time),
	}
	go bl.cleanupLoop(ctx)
	return bl
}

// Revoke marks jti as revoked until expiry. Writes to Redis and the in-memory
// cache; Redis errors are logged but do not block the request — the cache
// still protects this gateway instance.
func (bl *TokenBlocklist) Revoke(jti string, expiry time.Time) {
	if jti == "" || !expiry.After(time.Now()) {
		return
	}
	bl.mu.Lock()
	bl.entries[jti] = expiry
	bl.mu.Unlock()

	if bl.rdb != nil {
		ttl := time.Until(expiry)
		if ttl > 0 {
			ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
			defer cancel()
			if err := bl.rdb.Set(ctx, bl.keyPrefix+jti, "1", ttl).Err(); err != nil {
				slog.Warn("token blocklist redis set failed", "error", err, "jti", jti)
			}
		}
	}
}

// IsRevoked reports whether jti is currently revoked. Cache hit path is
// lock-only (no I/O); cache miss falls back to Redis with a tight timeout so
// a slow Redis can't stall request processing.
func (bl *TokenBlocklist) IsRevoked(jti string) bool {
	if jti == "" {
		return false
	}
	bl.mu.RLock()
	exp, ok := bl.entries[jti]
	bl.mu.RUnlock()
	if ok && time.Now().Before(exp) {
		return true
	}

	if bl.rdb != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
		defer cancel()
		n, err := bl.rdb.Exists(ctx, bl.keyPrefix+jti).Result()
		if err != nil {
			// Redis unavailable — fail open (don't lock everyone out). The
			// cache covers tokens revoked on this instance, and the JWT will
			// expire naturally via its exp claim.
			slog.Warn("token blocklist redis check failed, falling open", "error", err)
			return false
		}
		if n > 0 {
			// Persist into local cache so subsequent checks short-circuit.
			// We don't know the exact expiry without an extra TTL call; use
			// 1h as a safe upper bound (JWT is 3 days, but cache entries get
			// purged by the cleanup loop anyway).
			bl.mu.Lock()
			bl.entries[jti] = time.Now().Add(time.Hour)
			bl.mu.Unlock()
			return true
		}
	}

	return false
}

func (bl *TokenBlocklist) cleanupLoop(ctx context.Context) {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			bl.mu.Lock()
			now := time.Now()
			for jti, exp := range bl.entries {
				if now.After(exp) {
					delete(bl.entries, jti)
				}
			}
			bl.mu.Unlock()
		}
	}
}
