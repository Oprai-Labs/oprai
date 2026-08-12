package middleware

import (
	"encoding/json"
	"net/http"
	"strings"
)

// CSRFProtection returns a middleware that enforces two CSRF defences in depth:
//
//  1. X-Requested-With: XMLHttpRequest — a custom header a browser cross-site
//     form POST cannot set, blocking naive CSRF even when cookies carry auth.
//  2. Origin / Referer whitelist — when either header is present its value must
//     match allowedOrigin.  Absent Origin+Referer is allowed (non-browser
//     clients such as curl or server-side callers never send them).
//
// GET and HEAD are read-only and intentionally excluded from both checks.
// SameSite=Strict cookies provide the primary CSRF barrier; this is defence-in-depth.
func CSRFProtection(allowedOrigins string) func(http.Handler) http.Handler {
	allowed := splitOrigins(allowedOrigins)
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodGet || r.Method == http.MethodHead || r.Method == http.MethodOptions {
				next.ServeHTTP(w, r)
				return
			}

			// Browser CSP violation reports are POSTed with no X-Requested-With
			// and no credentials. They are not CSRF-attackable (the handler only
			// appends to a log) and the browser cannot be made to send the custom
			// header, so this path is exempt from the CSRF checks.
			if r.URL.Path == "/csp-report" {
				next.ServeHTTP(w, r)
				return
			}

			if r.Header.Get("X-Requested-With") != "XMLHttpRequest" {
				csrf403(w, "missing X-Requested-With header")
				return
			}

			if origin := r.Header.Get("Origin"); origin != "" {
				if !anyOriginMatches(origin, allowed) {
					csrf403(w, "origin not allowed")
					return
				}
			} else if referer := r.Header.Get("Referer"); referer != "" {
				if !anyRefererMatches(referer, allowed) {
					csrf403(w, "referer not allowed")
					return
				}
			}

			next.ServeHTTP(w, r)
		})
	}
}

func anyOriginMatches(origin string, allowed []string) bool {
	origin = strings.TrimRight(origin, "/")
	for _, a := range allowed {
		if strings.EqualFold(origin, strings.TrimRight(a, "/")) {
			return true
		}
	}
	return false
}

func anyRefererMatches(referer string, allowed []string) bool {
	referer = strings.TrimRight(referer, "/")
	for _, a := range allowed {
		a = strings.TrimRight(a, "/")
		if strings.EqualFold(referer, a) || strings.HasPrefix(strings.ToLower(referer), strings.ToLower(a)+"/") {
			return true
		}
	}
	return false
}

func csrf403(w http.ResponseWriter, reason string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": reason})
}
