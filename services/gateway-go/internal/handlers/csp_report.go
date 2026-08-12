package handlers

import (
	"io"
	"log/slog"
	"net/http"
)

// CSPReport records a browser Content-Security-Policy violation report.
//
// It is public and unauthenticated by necessity: browsers post these reports
// with no credentials and no X-Requested-With header (the CSRF middleware
// exempts /csp-report for that reason). The body is size-capped so a report
// storm cannot flood the logs, and the endpoint always answers 204 so the
// browser never retries.
//
// Used to validate a Content-Security-Policy-Report-Only rollout: real browsers
// report every blocked source here, so the policy can be confirmed complete
// before it is switched to enforcing.
func CSPReport(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(io.LimitReader(r.Body, 8<<10)) // 8 KB is plenty for a report
	if len(body) > 0 {
		slog.Warn("csp-violation",
			"report", string(body),
			"ua", r.Header.Get("User-Agent"),
		)
	}
	w.WriteHeader(http.StatusNoContent)
}
