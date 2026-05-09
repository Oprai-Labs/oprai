package config

import (
	"net/url"
	"os"
	"strconv"
)

// Config holds all admin-service configuration values.
type Config struct {
	Port            int
	GRPCPort        int
	DatabaseURL     string
	AdminJWTSecret  string
	AdminJWTTTL     int // seconds
	AdminCORSOrigin string
	Environment     string
	// InternalAPIKey is used to gate the /metrics endpoint so only internal
	// scrapers (Prometheus) can access it. Should match the shared key used
	// by the gateway and other services.
	InternalAPIKey string
	// TrustProxyHeaders controls whether X-Forwarded-For / X-Real-IP are trusted
	// for client IP extraction. Only set true when running behind a known reverse
	// proxy that strips or rewrites these headers. Defaults to false.
	TrustProxyHeaders bool
}

// Load reads configuration from environment variables with sensible defaults.
func Load() *Config {
	return &Config{
		Port:              getEnvInt("PORT", 3050),
		GRPCPort:          getEnvInt("GRPC_PORT", 50055),
		DatabaseURL:       getEnvStr("DATABASE_URL", buildDefaultDatabaseURL()),
		AdminJWTSecret:    getEnvStr("OPRAI_ADMIN_JWT_SECRET", "dev-admin-secret-change"),
		AdminJWTTTL:       getEnvInt("ADMIN_JWT_TTL_SECONDS", 28800), // 8 hours
		AdminCORSOrigin:   getEnvStr("ADMIN_CORS_ORIGIN", "http://localhost:3200"),
		Environment:       getEnvStr("NODE_ENV", "development"),
		InternalAPIKey:    getEnvStr("OPRAI_INTERNAL_API_KEY", ""),
		TrustProxyHeaders: getEnvStr("TRUST_PROXY_HEADERS", "false") == "true",
	}
}

func buildDefaultDatabaseURL() string {
	host := getEnvStr("DB_HOST", "localhost")
	port := getEnvStr("DB_PORT", "5433")
	name := getEnvStr("DB_NAME", "oprai")
	user := getEnvStr("DB_USER", "postgres")
	pass := getEnvStr("DB_PASSWORD", getEnvStr("DB_SUPERPASS", ""))
	sslMode := "require"
	if env := os.Getenv("NODE_ENV"); env == "development" || env == "" {
		sslMode = "prefer"
	}
	u := &url.URL{
		Scheme:   "postgres",
		User:     url.UserPassword(user, pass),
		Host:     host + ":" + port,
		Path:     "/" + name,
		RawQuery: "sslmode=" + sslMode,
	}
	return u.String()
}

func getEnvStr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getEnvInt(key string, fallback int) int {
	v := os.Getenv(key)
	if v == "" {
		return fallback
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return fallback
	}
	return n
}
