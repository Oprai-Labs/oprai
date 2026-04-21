package middleware

import "net/http"

// SecurityHeaders adds standard HTTP security headers to every response.
// These complement CORS and protect against common browser attacks.
func SecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Frame-Options", "DENY")
		h.Set("X-Content-Type-Options", "nosniff")
		// X-XSS-Protection: disabled in favour of CSP (modern browsers ignore it; old IE may re-enable XSS auditor bugs)
		h.Set("X-XSS-Protection", "0")
		h.Set("Referrer-Policy", "strict-origin-when-cross-origin")
		h.Set("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
		// HSTS: tell browsers to only connect over HTTPS for 1 year (preload-ready)
		h.Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
		// CSP: this service is a pure JSON API — no HTML, no scripts, no external resources
		h.Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		next.ServeHTTP(w, r)
	})
}
