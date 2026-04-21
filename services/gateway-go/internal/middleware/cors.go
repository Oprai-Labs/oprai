package middleware

import (
	"net/http"
	"strings"

	"github.com/go-chi/cors"
)

func splitOrigins(origins string) []string {
	parts := strings.Split(origins, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if s := strings.TrimSpace(p); s != "" {
			out = append(out, s)
		}
	}
	return out
}

// CORSMiddleware creates a CORS handler configured for the given origins.
// origins is a comma-separated list of allowed origins.
// AllowedOrigins must be specific (not "*") when AllowCredentials is true.
func CORSMiddleware(origins string) func(http.Handler) http.Handler {
	allowed := splitOrigins(origins)
	return cors.Handler(cors.Options{
		AllowedOrigins:   allowed,
		AllowedMethods:   []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Accept", "Authorization", "Content-Type", "X-Requested-With"},
		ExposedHeaders:   []string{"X-Request-Id"},
		AllowCredentials: true,
		MaxAge:           300,
	})
}
