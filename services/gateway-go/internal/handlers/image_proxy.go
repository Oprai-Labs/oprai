package handlers

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ImageProxy fetches a remote image server-side and re-serves it from the
// gateway origin. Token / NFT logos are frequently hosted on twimg.com, IPFS,
// or arweave — hosts that browser tracker/ad filters block, so a raw <img> to
// them fails even though the URL is valid. Proxying first-party (app.oprai.xyz)
// makes them always render. GET-only, so CSRF-exempt; loaded via <img>.
type ImageProxy struct {
	client *http.Client
}

// NewImageProxy builds an ImageProxy with a bounded HTTP client.
func NewImageProxy() *ImageProxy {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DialContext = safeDialContext() // pin the validated IP (anti DNS-rebind)
	return &ImageProxy{
		client: &http.Client{
			Timeout:   8 * time.Second,
			Transport: transport,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				if len(via) >= 5 {
					return http.ErrUseLastResponse
				}
				// Re-validate the redirect target against the SSRF guard.
				if isBlockedHost(req.URL.Hostname()) {
					return http.ErrUseLastResponse
				}
				return nil
			},
		},
	}
}

const maxImageBytes = 6 << 20 // 6 MB

// Proxy handles GET /img?url=<encoded>. Streams the upstream image back with a
// long cache. Rejects non-image content and private/loopback targets (SSRF).
func (p *ImageProxy) Proxy(w http.ResponseWriter, r *http.Request) {
	raw := r.URL.Query().Get("url")
	if raw == "" {
		http.Error(w, "missing url", http.StatusBadRequest)
		return
	}
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		http.Error(w, "bad url", http.StatusBadRequest)
		return
	}
	if isBlockedHost(u.Hostname()) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		http.Error(w, "internal", http.StatusInternalServerError)
		return
	}
	req.Header.Set("User-Agent", "oprai-image-proxy/1.0")
	req.Header.Set("Accept", "image/*")

	resp, err := p.client.Do(req)
	if err != nil {
		http.Error(w, "upstream error", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	ct := resp.Header.Get("Content-Type")
	if resp.StatusCode != http.StatusOK || !strings.HasPrefix(ct, "image/") {
		http.Error(w, "not an image", http.StatusBadGateway)
		return
	}

	w.Header().Set("Content-Type", ct)
	w.Header().Set("Cache-Control", "public, max-age=86400")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)
	_, _ = io.Copy(w, io.LimitReader(resp.Body, maxImageBytes))
}

// isBlockedHost blocks internal names and private / loopback / link-local IPs to
// prevent the image proxy from being used for SSRF.
func isBlockedHost(host string) bool {
	h := strings.ToLower(host)
	if h == "" || h == "localhost" || strings.HasSuffix(h, ".local") || strings.HasSuffix(h, ".internal") {
		return true
	}
	// Literal IP?
	if ip := net.ParseIP(h); ip != nil {
		return isBlockedIP(ip)
	}
	ips, err := net.LookupIP(host)
	if err != nil || len(ips) == 0 {
		return true // can't resolve → block
	}
	for _, ip := range ips {
		if isBlockedIP(ip) {
			return true
		}
	}
	return false
}

func isBlockedIP(ip net.IP) bool {
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
		ip.IsLinkLocalMulticast() || ip.IsUnspecified()
}
