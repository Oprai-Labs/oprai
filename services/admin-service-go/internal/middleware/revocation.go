package middleware

import (
	"crypto/sha256"
	"encoding/hex"
	"sync"
	"time"
)

// tokenRevocation is an in-memory denylist of admin JWTs that were logged out
// before their natural expiry.
//
// The admin service is single-instance and admin tokens live at most a few
// hours, so an in-memory set (cleared on restart) is sufficient — a restart is
// itself a full revocation of every outstanding token. Without this, "logout"
// only dropped the client cookie, so a captured token stayed valid for its full
// TTL with no way to kill it.
type tokenRevocation struct {
	mu      sync.RWMutex
	revoked map[string]time.Time // token hash -> the moment it may be forgotten (>= its exp)
}

var revocations = &tokenRevocation{revoked: make(map[string]time.Time)}

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

// RevokeAdminToken marks a token as logged-out until `until` (use its expiry, or
// now+TTL — either fully covers the token's remaining life). Also opportunistically
// drops entries that have themselves expired so the map cannot grow unbounded.
func RevokeAdminToken(token string, until time.Time) {
	h := hashToken(token)
	revocations.mu.Lock()
	revocations.revoked[h] = until
	now := time.Now()
	for k, exp := range revocations.revoked {
		if now.After(exp) {
			delete(revocations.revoked, k)
		}
	}
	revocations.mu.Unlock()
}

// IsAdminTokenRevoked reports whether the token was logged out and has not yet
// passed its recorded expiry.
func IsAdminTokenRevoked(token string) bool {
	h := hashToken(token)
	revocations.mu.RLock()
	exp, ok := revocations.revoked[h]
	revocations.mu.RUnlock()
	return ok && time.Now().Before(exp)
}
