package services

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

const revocationKeyPrefix = "oprai:revoked_jti:"

// RevocationService stores revoked JWT IDs so that logged-out tokens are
// rejected on subsequent requests. Redis is the primary store with an
// in-memory fallback for development environments without Redis.
type RevocationService struct {
	redis    *redis.Client
	mu       sync.RWMutex
	fallback map[string]time.Time // jti → expiry
}

// NewRevocationService creates a RevocationService backed by the provided
// Redis client. Pass nil to use the in-memory fallback only.
func NewRevocationService(rdb *redis.Client) *RevocationService {
	rs := &RevocationService{
		redis:    rdb,
		fallback: make(map[string]time.Time),
	}
	go rs.cleanupLoop()
	return rs
}

// Revoke marks a jti as revoked until the given expiry time.
// Callers should pass the token's own expiry so the entry self-expires.
func (rs *RevocationService) Revoke(ctx context.Context, jti string, expiry time.Time) {
	ttl := time.Until(expiry)
	if ttl <= 0 {
		return // already expired — no need to store
	}

	if rs.redis != nil {
		key := revocationKeyPrefix + jti
		if err := rs.redis.Set(ctx, key, "1", ttl).Err(); err != nil {
			slog.Warn("RevocationService: Redis SET failed, using in-memory fallback",
				"jti", jti, "error", err)
		} else {
			return
		}
	}

	rs.mu.Lock()
	rs.fallback[jti] = expiry
	rs.mu.Unlock()
}

// IsRevoked returns true if the jti has been revoked.
func (rs *RevocationService) IsRevoked(ctx context.Context, jti string) bool {
	if jti == "" {
		return false
	}

	if rs.redis != nil {
		key := revocationKeyPrefix + jti
		exists, err := rs.redis.Exists(ctx, key).Result()
		if err == nil {
			return exists > 0
		}
		slog.Warn("RevocationService: Redis EXISTS failed, falling back to in-memory",
			"jti", jti, "error", err)
	}

	rs.mu.RLock()
	exp, ok := rs.fallback[jti]
	rs.mu.RUnlock()
	if !ok {
		return false
	}
	return time.Now().Before(exp)
}

func (rs *RevocationService) cleanupLoop() {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for range ticker.C {
		rs.mu.Lock()
		now := time.Now()
		for jti, exp := range rs.fallback {
			if now.After(exp) {
				delete(rs.fallback, jti)
			}
		}
		rs.mu.Unlock()
	}
}
