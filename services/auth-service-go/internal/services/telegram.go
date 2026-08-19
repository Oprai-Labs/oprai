package services

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

// TelegramAuthMaxAge bounds how stale a Login Widget payload may be. Telegram
// recommends rejecting old auth_date values to stop replay of a leaked payload.
const TelegramAuthMaxAge = 24 * time.Hour

// VerifyTelegramAuth validates a Telegram Login Widget payload against the bot
// token, per https://core.telegram.org/widgets/login#checking-authorization.
//
// data is the widget's fields (id, first_name, username, photo_url, auth_date,
// hash, …). It returns the verified telegram user id (as a string) on success.
//
// The check: secret = SHA256(botToken); the data-check-string is every field
// except `hash`, "key=value" sorted by key and joined with "\n"; the payload is
// authentic iff HMAC-SHA256(data-check-string, secret) == hash.
func VerifyTelegramAuth(botToken string, data map[string]string) (string, error) {
	if botToken == "" {
		return "", fmt.Errorf("telegram linking is not configured")
	}
	providedHash := strings.ToLower(strings.TrimSpace(data["hash"]))
	if providedHash == "" {
		return "", fmt.Errorf("missing hash")
	}
	id := strings.TrimSpace(data["id"])
	if id == "" {
		return "", fmt.Errorf("missing telegram id")
	}

	// Freshness: reject stale payloads to bound replay.
	if authDate := data["auth_date"]; authDate != "" {
		if ts, err := strconv.ParseInt(authDate, 10, 64); err == nil {
			if time.Since(time.Unix(ts, 0)) > TelegramAuthMaxAge {
				return "", fmt.Errorf("authorization expired")
			}
		}
	}

	// Build the data-check-string from all fields except `hash`.
	keys := make([]string, 0, len(data))
	for k := range data {
		if k == "hash" {
			continue
		}
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+data[k])
	}
	checkString := strings.Join(parts, "\n")

	secret := sha256.Sum256([]byte(botToken))
	mac := hmac.New(sha256.New, secret[:])
	mac.Write([]byte(checkString))
	expected := hex.EncodeToString(mac.Sum(nil))

	if !hmac.Equal([]byte(expected), []byte(providedHash)) {
		return "", fmt.Errorf("invalid telegram signature")
	}
	return id, nil
}
