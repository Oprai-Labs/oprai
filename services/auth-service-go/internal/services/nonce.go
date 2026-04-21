package services

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

const redisKeyPrefix = "oprai:nonce:"

// NonceResult is returned from Generate.
type NonceResult struct {
	Nonce   string `json:"nonce"`
	NonceID string `json:"nonceId"`
}

// NonceService manages nonce generation and consumption for SIWS auth.
// It uses Redis as the primary store with an in-memory fallback.
type NonceService struct {
	redis    *redis.Client
	ttl      time.Duration
	mu       sync.Mutex
	fallback map[string]fallbackEntry
}

type fallbackEntry struct {
	nonce     string
	expiresAt time.Time
}

// NewNonceService creates a NonceService with the given Redis client and TTL.
// If rdb is nil, only the in-memory fallback is used.
func NewNonceService(rdb *redis.Client, ttlSeconds int) *NonceService {
	ns := &NonceService{
		redis:    rdb,
		ttl:      time.Duration(ttlSeconds) * time.Second,
		fallback: make(map[string]fallbackEntry),
	}

	// Start periodic cleanup of the in-memory fallback store
	go ns.cleanupLoop()

	return ns
}

// Generate creates a new random nonce and stores it keyed by a unique nonceId.
func (ns *NonceService) Generate(ctx context.Context) (*NonceResult, error) {
	// Generate 16 random bytes as hex = 32-char nonce
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return nil, fmt.Errorf("generating nonce random bytes: %w", err)
	}
	nonce := hex.EncodeToString(raw)
	nonceID := uuid.New().String()

	// Try Redis first
	if ns.redis != nil {
		key := redisKeyPrefix + nonceID
		err := ns.redis.Set(ctx, key, nonce, ns.ttl).Err()
		if err == nil {
			return &NonceResult{Nonce: nonce, NonceID: nonceID}, nil
		}
		slog.Warn("Redis SET failed for nonce, falling back to in-memory store",
			"nonce_id", nonceID,
			"error", err,
		)
	}

	// Fallback: in-memory store
	ns.mu.Lock()
	ns.fallback[nonceID] = fallbackEntry{
		nonce:     nonce,
		expiresAt: time.Now().Add(ns.ttl),
	}
	ns.mu.Unlock()

	return &NonceResult{Nonce: nonce, NonceID: nonceID}, nil
}

// Consume retrieves and deletes the nonce for the given nonceId.
// Returns the nonce string, or empty string if not found or expired.
func (ns *NonceService) Consume(ctx context.Context, nonceID string) (string, error) {
	// Try Redis first
	if ns.redis != nil {
		key := redisKeyPrefix + nonceID

		// Use GET + DEL pipeline for atomic consume
		pipe := ns.redis.Pipeline()
		getCmd := pipe.Get(ctx, key)
		pipe.Del(ctx, key)

		_, err := pipe.Exec(ctx)
		if err != nil && err != redis.Nil {
			slog.Warn("Redis consume pipeline failed, falling back to in-memory store",
				"nonce_id", nonceID,
				"error", err,
			)
			return ns.fallbackConsume(nonceID), nil
		}

		nonce, err := getCmd.Result()
		if err == redis.Nil {
			// Not in Redis, try fallback
			return ns.fallbackConsume(nonceID), nil
		}
		if err != nil {
			slog.Warn("Redis GET failed during nonce consume",
				"nonce_id", nonceID,
				"error", err,
			)
			return ns.fallbackConsume(nonceID), nil
		}

		return nonce, nil
	}

	return ns.fallbackConsume(nonceID), nil
}

// fallbackConsume retrieves and removes a nonce from the in-memory store.
func (ns *NonceService) fallbackConsume(nonceID string) string {
	ns.mu.Lock()
	defer ns.mu.Unlock()

	entry, ok := ns.fallback[nonceID]
	if !ok {
		return ""
	}
	delete(ns.fallback, nonceID)

	if time.Now().After(entry.expiresAt) {
		return ""
	}
	return entry.nonce
}

// cleanupLoop removes expired entries from the in-memory fallback every 60s.
func (ns *NonceService) cleanupLoop() {
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		ns.mu.Lock()
		now := time.Now()
		for id, entry := range ns.fallback {
			if now.After(entry.expiresAt) {
				delete(ns.fallback, id)
			}
		}
		ns.mu.Unlock()
	}
}
