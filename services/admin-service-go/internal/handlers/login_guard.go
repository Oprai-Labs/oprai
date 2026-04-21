package handlers

import (
	"fmt"
	"sync"
	"time"
)

// loginGuard tracks failed login attempts per IP and per username.
// It provides both per-IP rate limiting (block one attacker trying many accounts)
// and per-username limiting (block distributed attacks against one account).
//
// State is in-memory and resets on service restart. This is acceptable for the
// admin service — the window is short (15 min) and restart itself is an event.
type loginGuard struct {
	mu      sync.Mutex
	entries map[string]*guardEntry
}

type guardEntry struct {
	attempts    int
	windowStart time.Time
	lockedUntil time.Time
}

const (
	maxAttempts    = 5
	windowDuration = 15 * time.Minute
	lockDuration   = 15 * time.Minute
)

var guard = &loginGuard{
	entries: make(map[string]*guardEntry),
}

// check returns an error message and retry-after seconds if the key is
// currently blocked. A nil error means the attempt is allowed to proceed.
func (g *loginGuard) check(key string) (blocked bool, retryAfterSecs int) {
	g.mu.Lock()
	defer g.mu.Unlock()

	e := g.entry(key)
	if time.Now().Before(e.lockedUntil) {
		secs := int(time.Until(e.lockedUntil).Seconds()) + 1
		return true, secs
	}
	return false, 0
}

// failure records a failed attempt for key. Returns true if the key is now
// locked (this failure pushed it over the threshold).
func (g *loginGuard) failure(key string) (nowLocked bool) {
	g.mu.Lock()
	defer g.mu.Unlock()

	e := g.entry(key)

	// Reset window if it has expired.
	if time.Since(e.windowStart) > windowDuration {
		e.attempts = 0
		e.windowStart = time.Now()
	}

	e.attempts++
	if e.attempts >= maxAttempts {
		e.lockedUntil = time.Now().Add(lockDuration)
		e.attempts = 0 // reset counter so next window starts fresh after unlock
		return true
	}
	return false
}

// success clears the attempt counter for key on a successful login.
func (g *loginGuard) success(key string) {
	g.mu.Lock()
	defer g.mu.Unlock()

	delete(g.entries, key)
}

// remaining returns how many attempts are left before lockout for the key.
func (g *loginGuard) remaining(key string) int {
	g.mu.Lock()
	defer g.mu.Unlock()

	e := g.entry(key)
	if time.Since(e.windowStart) > windowDuration {
		return maxAttempts
	}
	r := maxAttempts - e.attempts
	if r < 0 {
		return 0
	}
	return r
}

// entry returns (or creates) the entry for key. Must be called with g.mu held.
func (g *loginGuard) entry(key string) *guardEntry {
	if e, ok := g.entries[key]; ok {
		return e
	}
	e := &guardEntry{windowStart: time.Now()}
	g.entries[key] = e
	return e
}

// ipKey and usernameKey produce the lookup keys used for the two tracking axes.
func ipKey(ip string) string       { return "ip:" + ip }
func usernameKey(u string) string  { return "user:" + u }

// checkLogin verifies that both the IP and the username are not currently
// locked out. Returns a human-readable reason and retry-after seconds on block.
func checkLogin(ip, username string) (blocked bool, reason string, retryAfterSecs int) {
	if blocked, secs := guard.check(ipKey(ip)); blocked {
		return true, fmt.Sprintf("Too many failed attempts from this IP. Try again in %d seconds.", secs), secs
	}
	if blocked, secs := guard.check(usernameKey(username)); blocked {
		return true, fmt.Sprintf("Account temporarily locked due to too many failed attempts. Try again in %d seconds.", secs), secs
	}
	return false, "", 0
}

// recordFailure increments the failure counter for both IP and username.
// Returns a human-readable warning if approaching the limit.
func recordFailure(ip, username string) string {
	guard.failure(ipKey(ip))
	guard.failure(usernameKey(username))

	rem := guard.remaining(usernameKey(username))
	if rem == 0 {
		return fmt.Sprintf("Account locked for %d minutes due to too many failed attempts.", int(lockDuration.Minutes()))
	}
	return fmt.Sprintf("Invalid credentials. %d attempt(s) remaining before account lockout.", rem)
}

// recordSuccess clears counters for both IP and username after a successful login.
func recordSuccess(ip, username string) {
	guard.success(ipKey(ip))
	guard.success(usernameKey(username))
}
