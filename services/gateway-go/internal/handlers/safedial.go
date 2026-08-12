package handlers

import (
	"context"
	"fmt"
	"net"
	"time"
)

// safeDialContext returns a DialContext that closes the SSRF DNS-rebinding gap
// in the image proxy/cache.
//
// The handlers pre-validate a URL's host by resolving it and rejecting private/
// loopback/link-local addresses, but then hand the original hostname to the HTTP
// client, which resolves it AGAIN at dial time. A short-TTL attacker domain can
// answer a public IP on the first lookup (passing the pre-check) and an internal
// IP on the second (the dial) — classic DNS rebinding. This dialer removes the
// gap: it resolves the host itself, blocks the connection if ANY resolved
// address is disallowed, and dials the exact validated IP, so the address that
// passed the check is the one actually connected to. TLS still uses the original
// hostname for SNI/cert verification, so HTTPS to legitimate CDNs is unaffected.
func safeDialContext() func(ctx context.Context, network, addr string) (net.Conn, error) {
	d := &net.Dialer{Timeout: 5 * time.Second}
	return func(ctx context.Context, network, addr string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(addr)
		if err != nil {
			return nil, err
		}
		ips, err := net.DefaultResolver.LookupIP(ctx, "ip", host)
		if err != nil || len(ips) == 0 {
			return nil, fmt.Errorf("blocked: cannot resolve host %q", host)
		}
		for _, ip := range ips {
			if isBlockedIP(ip) {
				return nil, fmt.Errorf("blocked: host %q resolves to a disallowed address", host)
			}
		}
		// Dial the exact IP we just validated — no second, unvalidated resolution.
		return d.DialContext(ctx, network, net.JoinHostPort(ips[0].String(), port))
	}
}
