package services

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"testing"
	"time"
)

func signTelegram(botToken string, data map[string]string) string {
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
	secret := sha256.Sum256([]byte(botToken))
	mac := hmac.New(sha256.New, secret[:])
	mac.Write([]byte(strings.Join(parts, "\n")))
	return hex.EncodeToString(mac.Sum(nil))
}

func TestVerifyTelegramAuth_RoundTrip(t *testing.T) {
	const token = "123456:test-bot-token"
	data := map[string]string{
		"id":         "987654321",
		"first_name": "Ada",
		"username":   "ada",
		"auth_date":  fmt.Sprintf("%d", time.Now().Unix()),
	}
	data["hash"] = signTelegram(token, data)

	id, err := VerifyTelegramAuth(token, data)
	if err != nil {
		t.Fatalf("valid payload rejected: %v", err)
	}
	if id != "987654321" {
		t.Fatalf("wrong id: %s", id)
	}

	// Tampered field breaks the HMAC.
	data["username"] = "mallory"
	if _, err := VerifyTelegramAuth(token, data); err == nil {
		t.Fatal("tampered payload accepted")
	}

	// Not configured.
	if _, err := VerifyTelegramAuth("", data); err == nil {
		t.Fatal("empty bot token should fail")
	}
}

func TestVerifyTelegramAuth_Stale(t *testing.T) {
	const token = "123456:test-bot-token"
	data := map[string]string{
		"id":        "1",
		"auth_date": fmt.Sprintf("%d", time.Now().Add(-48*time.Hour).Unix()),
	}
	data["hash"] = signTelegram(token, data)
	if _, err := VerifyTelegramAuth(token, data); err == nil {
		t.Fatal("stale payload accepted")
	}
}
