package middleware

import (
	"context"
	"sync"
	"time"
)

// TokenBlocklist is a thread-safe in-memory store for revoked JWT IDs.
// When a user logs out, the gateway adds the token's jti here so that
// Bearer copies (e.g. from localStorage) are immediately invalidated
// without waiting for the natural expiry of the 1-hour token.
//
// Note: this is per-process state. A multi-instance deployment should
// use a shared Redis-backed blocklist instead.
type TokenBlocklist struct {
	mu      sync.RWMutex
	entries map[string]time.Time // jti → expiry
}

// NewTokenBlocklist creates a TokenBlocklist and starts a background cleanup
// goroutine that stops when ctx is cancelled.
func NewTokenBlocklist(ctx context.Context) *TokenBlocklist {
	bl := &TokenBlocklist{
		entries: make(map[string]time.Time),
	}
	go bl.cleanupLoop(ctx)
	return bl
}

// Revoke marks jti as revoked until expiry.
func (bl *TokenBlocklist) Revoke(jti string, expiry time.Time) {
	if jti == "" || !expiry.After(time.Now()) {
		return
	}
	bl.mu.Lock()
	bl.entries[jti] = expiry
	bl.mu.Unlock()
}

// IsRevoked reports whether jti is currently in the blocklist.
func (bl *TokenBlocklist) IsRevoked(jti string) bool {
	if jti == "" {
		return false
	}
	bl.mu.RLock()
	exp, ok := bl.entries[jti]
	bl.mu.RUnlock()
	return ok && time.Now().Before(exp)
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
