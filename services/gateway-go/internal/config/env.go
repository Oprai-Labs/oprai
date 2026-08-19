package config

import (
	"os"
	"strconv"
)

// Config holds all gateway configuration values.
type Config struct {
	Port      int
	JWTSecret string
	// JWTPreviousSecret enables rolling rotation: set to the previous value of
	// OPRAI_JWT_SECRET via OPRAI_JWT_SECRET_OLD so tokens issued before the
	// rotation continue to validate until they expire. Empty when no rotation
	// is in flight.
	JWTPreviousSecret string
	InternalAPIKey    string
	CORSOrigin        string
	Environment       string
	// TrustProxyHeaders enables reading X-Forwarded-For / X-Real-IP for the real
	// client IP. Only set this to true when the gateway sits behind a trusted
	// reverse proxy (e.g. nginx, Cloudflare). In direct-internet or dev setups
	// leave it false to prevent IP spoofing via forged headers.
	TrustProxyHeaders bool

	// gRPC service addresses
	AuthServiceGRPC   string
	ChatServiceGRPC   string
	SolanaServiceGRPC string
	MemoryServiceGRPC string

	// HTTP service addresses (used for reverse-proxy until gRPC stubs are generated)
	AuthServiceHTTP   string
	ChatServiceHTTP   string
	SolanaServiceHTTP string
	MemoryServiceHTTP string

	// External API keys (proxied through gateway to protect from client exposure)
	BirdeyeAPIKey string
	JupiterAPIKey string
	HeliusAPIKey  string
	AlchemyAPIKey string // EVM wallet portfolio (balances + prices, multichain)

	// Local file upload storage
	UploadDir     string // Local directory to store uploaded files
	PublicBaseURL string // Public base URL for uploaded files (e.g. https://api.oprai.xyz)

	// Redis URL — backs the JWT revocation blocklist (so revocations survive
	// gateway restarts and are shared across instances). When empty, the
	// blocklist degrades to in-memory only.
	RedisURL string
}

// Load reads configuration from environment variables with sensible defaults.
func Load() *Config {
	return &Config{
		Port:              getEnvInt("PORT", 3001),
		JWTSecret:         getEnvStr("OPRAI_JWT_SECRET", "dev-insecure-secret-change"),
		JWTPreviousSecret: getEnvStr("OPRAI_JWT_SECRET_OLD", ""),
		InternalAPIKey:    getEnvStr("OPRAI_INTERNAL_API_KEY", "dev-internal-key-change"),
		CORSOrigin:        getEnvStr("CORS_ORIGIN", "http://localhost:3000"),
		Environment:       getEnvStr("NODE_ENV", "development"),
		TrustProxyHeaders: getEnvBool("TRUST_PROXY_HEADERS", false),

		AuthServiceGRPC:   getEnvStr("AUTH_SERVICE_GRPC", "localhost:50051"),
		ChatServiceGRPC:   getEnvStr("CHAT_SERVICE_GRPC", "localhost:50052"),
		SolanaServiceGRPC: getEnvStr("SOLANA_SERVICE_GRPC", "localhost:50053"),
		MemoryServiceGRPC: getEnvStr("MEMORY_SERVICE_GRPC", "localhost:50054"),

		AuthServiceHTTP:   getEnvStr("AUTH_SERVICE_HTTP", "http://localhost:3010"),
		ChatServiceHTTP:   getEnvStr("CHAT_SERVICE_HTTP", "http://localhost:3020"),
		SolanaServiceHTTP: getEnvStr("SOLANA_SERVICE_HTTP", "http://localhost:3030"),
		MemoryServiceHTTP: getEnvStr("MEMORY_SERVICE_HTTP", "http://localhost:3040"),

		BirdeyeAPIKey: getEnvStr("BIRDEYE_API_KEY", ""),
		JupiterAPIKey: getEnvStr("JUPITER_API_KEY", ""),
		HeliusAPIKey:  getEnvStr("HELIUS_API_KEY", ""),
		AlchemyAPIKey: getEnvStr("ALCHEMY_API_KEY", ""),

		UploadDir:     getEnvStr("UPLOAD_DIR", "./uploads"),
		PublicBaseURL: getEnvStr("PUBLIC_BASE_URL", "http://localhost:3001"),

		RedisURL: getEnvStr("REDIS_URL", "redis://localhost:6379"),
	}
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

func getEnvBool(key string, fallback bool) bool {
	v := os.Getenv(key)
	if v == "" {
		return fallback
	}
	return v == "true" || v == "1" || v == "yes"
}
