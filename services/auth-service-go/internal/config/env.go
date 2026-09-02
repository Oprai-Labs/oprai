package config

import (
	"fmt"
	"os"
	"strconv"
)

// Config holds all environment-driven configuration for the auth service.
type Config struct {
	Port      int
	GRPCPort  int
	JWTSecret string
	// JWTPreviousSecret is the previous signing key during a rolling rotation.
	// New tokens are signed with JWTSecret; tokens carrying the old signature
	// are accepted via JWTPreviousSecret until they expire naturally. Empty
	// when no rotation is in flight. See `services.JWTService` doc.
	JWTPreviousSecret string
	InternalAPIKey    string
	DatabaseURL       string
	RedisURL          string
	DBSchema          string
	Environment       string
	CORSOrigin        string // Allowed CORS origin for the gateway (default: http://localhost:3001)

	// AppDomain is the canonical host users sign into (e.g. "app.oprai.xyz").
	// When set, the domain-bound login message (EIP-4361/SIWS) MUST carry it, so
	// a signature phished on another origin can't be replayed to us. Empty =
	// domain not enforced (dev), only the single-use nonce + signature are
	// checked. Set APP_DOMAIN in prod.
	AppDomain string

	// TelegramBotToken enables Telegram Login Widget verification for linking a
	// Telegram identity to an account. Empty = Telegram linking is disabled and
	// the endpoint returns a clear "not configured" error.
	TelegramBotToken string

	// TelegramBotUsername (without @) builds the deep-link the web app hands
	// users to link their Telegram: https://t.me/<username>?start=<token>.
	TelegramBotUsername string

	// TrustProxyHeaders controls whether X-Forwarded-For / X-Real-IP headers
	// are trusted for client IP extraction. Set to true only when the service
	// is known to run behind a trusted reverse proxy. When false (the default),
	// r.RemoteAddr is always used, preventing IP spoofing via headers.
	TrustProxyHeaders bool

	// AuthRateLimitAttempts / AuthRateLimitWindowSeconds bound the per-IP
	// attempts on /auth/nonce and /auth/verify. The defaults (10 per 15 min)
	// are the production posture; an automated test suite legitimately makes
	// more than that in a burst, so a dev environment can raise them via
	// AUTH_RATE_LIMIT_ATTEMPTS / AUTH_RATE_LIMIT_WINDOW_SECONDS.
	AuthRateLimitAttempts      int
	AuthRateLimitWindowSeconds int

	// Derived constants
	SessionTTLSeconds int // configurable via SESSION_TTL_SECONDS; default 3 days
	NonceTTLSeconds   int // 10 minutes = 600
}

// InsecureDefaultJWTSecret is the dev-only default. Blocked in production.
const InsecureDefaultJWTSecret = "dev-insecure-secret-change"

// InsecureDefaultInternalKey is the dev-only default. Blocked in production.
const InsecureDefaultInternalKey = "dev-internal-key-change"

// Load reads configuration from environment variables, returning an error
// if required values are missing or invalid for the current environment.
func Load() (*Config, error) {
	port := getEnvInt("PORT", 3010)
	grpcPort := getEnvInt("GRPC_PORT", 50051)

	jwtSecret := getEnv("OPRAI_JWT_SECRET", InsecureDefaultJWTSecret)
	jwtPreviousSecret := getEnv("OPRAI_JWT_SECRET_OLD", "")
	internalAPIKey := getEnv("OPRAI_INTERNAL_API_KEY", InsecureDefaultInternalKey)
	databaseURL := getEnv("DATABASE_URL", "")
	redisURL := getEnv("REDIS_URL", "redis://localhost:6379")
	dbSchema := getEnv("DB_SCHEMA", "auth_schema")
	corsOrigin := getEnv("CORS_ORIGIN", "http://localhost:3001") // Gateway origin
	telegramBotToken := getEnv("OPRAI_TELEGRAM_BOT_TOKEN", "")
	environment := getEnv("NODE_ENV", "development") // Keep NODE_ENV for compat with monorepo
	trustProxy := os.Getenv("TRUST_PROXY_HEADERS") == "true"

	// Also check GO_ENV for Go-native convention
	if env := os.Getenv("GO_ENV"); env != "" {
		environment = env
	}

	cfg := &Config{
		Port:              port,
		GRPCPort:          grpcPort,
		JWTSecret:         jwtSecret,
		JWTPreviousSecret: jwtPreviousSecret,
		InternalAPIKey:    internalAPIKey,
		DatabaseURL:       databaseURL,
		RedisURL:          redisURL,
		DBSchema:          dbSchema,
		CORSOrigin:        corsOrigin,
		AppDomain:         getEnv("APP_DOMAIN", ""),
		TelegramBotToken:  telegramBotToken,
		TelegramBotUsername: getEnv("OPRAI_TG_BOT_USERNAME", ""),
		Environment:       environment,
		TrustProxyHeaders: trustProxy,
		AuthRateLimitAttempts:      getEnvInt("AUTH_RATE_LIMIT_ATTEMPTS", 10),
		AuthRateLimitWindowSeconds: getEnvInt("AUTH_RATE_LIMIT_WINDOW_SECONDS", 15*60),
		SessionTTLSeconds: getEnvInt("SESSION_TTL_SECONDS", 60*60*24*3), // 3 days default
		NonceTTLSeconds:   60 * 10,                                      // 10 minutes
	}

	if err := cfg.validate(); err != nil {
		return nil, err
	}

	return cfg, nil
}

// IsProd returns true if the service is running in production mode.
func (c *Config) IsProd() bool {
	return c.Environment == "production"
}

func (c *Config) validate() error {
	if c.IsProd() {
		if c.JWTSecret == InsecureDefaultJWTSecret {
			return fmt.Errorf("FATAL: OPRAI_JWT_SECRET is set to the insecure default. Refusing to start in production")
		}
		if len(c.JWTSecret) < 32 {
			return fmt.Errorf("FATAL: OPRAI_JWT_SECRET must be at least 32 characters in production")
		}
		if c.InternalAPIKey == InsecureDefaultInternalKey {
			return fmt.Errorf("FATAL: OPRAI_INTERNAL_API_KEY is set to the insecure default. Refusing to start in production")
		}
		if len(c.InternalAPIKey) < 32 {
			return fmt.Errorf("FATAL: OPRAI_INTERNAL_API_KEY must be at least 32 characters in production")
		}
		// Reject obviously-broken rotation states. A previous secret equal to
		// the current one means rotation forgot to update the new one;
		// fail fast rather than silently accepting either.
		if c.JWTPreviousSecret != "" {
			if c.JWTPreviousSecret == c.JWTSecret {
				return fmt.Errorf("FATAL: OPRAI_JWT_SECRET_OLD must differ from OPRAI_JWT_SECRET during rotation")
			}
			if len(c.JWTPreviousSecret) < 32 {
				return fmt.Errorf("FATAL: OPRAI_JWT_SECRET_OLD must be at least 32 characters in production")
			}
		}
		if c.DatabaseURL == "" {
			return fmt.Errorf("FATAL: DATABASE_URL is required in production")
		}
	}

	if c.JWTSecret == InsecureDefaultJWTSecret {
		fmt.Println("WARNING: Using insecure default JWT secret. Set OPRAI_JWT_SECRET in production!")
	} else if len(c.JWTSecret) < 32 {
		fmt.Println("WARNING: OPRAI_JWT_SECRET is shorter than 32 characters. Use a longer secret in production!")
	}
	if c.InternalAPIKey == InsecureDefaultInternalKey {
		fmt.Println("WARNING: Using insecure default internal API key. Set OPRAI_INTERNAL_API_KEY in production!")
	} else if len(c.InternalAPIKey) < 32 {
		fmt.Println("WARNING: OPRAI_INTERNAL_API_KEY is shorter than 32 characters. Use a longer key in production!")
	}

	return nil
}

func getEnv(key, fallback string) string {
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
