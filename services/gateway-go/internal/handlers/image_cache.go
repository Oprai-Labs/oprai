package handlers

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	"image/draw"
	_ "image/gif"
	"image/jpeg"
	"image/png"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	xdraw "golang.org/x/image/draw"
)

// Token logos are hosted by whoever issued the token, and some of those hosts
// are slow enough to be indistinguishable from broken. The GILTS and CETES
// logos are 621 KB PNGs behind an S3 bucket that trickles them out over a
// minute or more; the card renders, the circle stays empty, and the user reads
// that as a bug — which, from where they sit, it is.
//
// This caches them on our side. The first request is served by REDIRECTING to
// the origin, so nothing is ever slower than it is today, while a background
// fetch warms the cache. Every request after that is served from Finland at
// our bandwidth instead of the issuer's.
//
// Not an open proxy: https only, no private-network destinations (so it can't
// be pointed at our own internal services), image content types only, and a
// hard size cap.
const (
	imageCacheTTL     = 24 * time.Hour
	imageFailTTL      = 10 * time.Minute
	imageFetchTimeout = 45 * time.Second
	// Generous, because the input is not what we store. A Mad Lads PNG is
	// 4.7 MB — over the 4 MB this used to allow, so every one of them was
	// rejected and negatively cached, and the grid fell back to pulling all
	// twelve originals from the issuer. What we keep is the downscaled copy,
	// so the cost of a large input is one resize, not memory.
	imageMaxBytes = 24 << 20
	// Default for token logos: ~3x the 28px circle they are drawn in.
	imageTargetPx = 96
	// NFT art is drawn at ~150px in the grid, so it needs a bigger cache
	// entry. Serving those at 96 makes the whole grid look soft.
	imageMaxTargetPx = 512
	imageWaitFirst   = 1500 * time.Millisecond
)

type cachedImage struct {
	ContentType string `json:"ct"`
	Body        []byte `json:"b"`
}

// inFlightImages keeps a single warm-up per cache key, so a card with twenty
// rows pointing at the same slow host doesn't start twenty identical
// downloads.
// The channel closes when that warm-up finishes, so a request can wait a
// moment for it instead of bouncing the browser to the origin.
var inFlightImages sync.Map // cache key -> chan struct{}

// GetTokenImage serves a cached token logo, or redirects to the origin while
// it warms. GET /market/token-image?url=<absolute https url>
func (m *MarketProxy) GetTokenImage(w http.ResponseWriter, r *http.Request) {
	raw := r.URL.Query().Get("url")
	if raw == "" {
		writeError(w, http.StatusBadRequest, "url parameter required")
		return
	}
	target, err := url.Parse(raw)
	if err != nil || target.Scheme != "https" || target.Host == "" {
		writeError(w, http.StatusBadRequest, "url must be an absolute https URL")
		return
	}
	if isPrivateHost(target.Hostname()) {
		writeError(w, http.StatusBadRequest, "url not allowed")
		return
	}

	// One URL can be wanted at two sizes — a 28px token logo and a 150px NFT
	// tile — so the size is part of the key. Without it whichever request
	// arrived first would decide how sharp the other one looks.
	size := imageTargetPx
	if n, err := strconv.Atoi(r.URL.Query().Get("size")); err == nil && n > 0 {
		size = min(n, imageMaxTargetPx)
	}
	key := fmt.Sprintf("img:%d:%s", size, hashURL(raw))
	if m.serveCachedImage(w, key) {
		return
	}

	// Not cached. Start the warm-up and give it a moment — most logos arrive
	// well inside this, and then even the FIRST viewer gets the small cached
	// copy instead of the issuer's original.
	done := m.warmImage(raw, key, size)
	select {
	case <-done:
		if m.serveCachedImage(w, key) {
			return
		}
	case <-time.After(imageWaitFirst):
	case <-r.Context().Done():
		return
	}

	// Slow or failed origin: redirect, so this request is never worse than
	// going direct. The warm-up carries on, and the next view is served here.
	http.Redirect(w, r, raw, http.StatusFound)
}

// serveCachedImage writes a cached logo if there is one. A cached FAILURE is
// stored as an empty body and reports false, so the caller falls through to
// the redirect — the origin may have recovered, and its own error is the one
// worth showing the browser.
func (m *MarketProxy) serveCachedImage(w http.ResponseWriter, key string) bool {
	data, ok := m.cache.Get(key)
	if !ok {
		return false
	}
	var img cachedImage
	if json.Unmarshal(data, &img) != nil || len(img.Body) == 0 {
		return false
	}
	w.Header().Set("Content-Type", img.ContentType)
	w.Header().Set("Cache-Control", "public, max-age=86400")
	w.Header().Set("X-Cache", "HIT")
	_, _ = w.Write(img.Body)
	return true
}

// warmImage downloads the image in the background, once per URL. The returned
// channel closes when the download finishes, however it finished.
func (m *MarketProxy) warmImage(raw, key string, size int) <-chan struct{} {
	ch := make(chan struct{})
	if existing, busy := inFlightImages.LoadOrStore(key, ch); busy {
		return existing.(chan struct{})
	}
	go func() {
		defer func() {
			inFlightImages.Delete(key)
			close(ch)
		}()

		client := &http.Client{
			Timeout: imageFetchTimeout,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				if len(via) >= 3 {
					return http.ErrUseLastResponse
				}
				// A redirect is another chance to be pointed somewhere internal.
				if req.URL.Scheme != "https" || isPrivateHost(req.URL.Hostname()) {
					return http.ErrUseLastResponse
				}
				return nil
			},
		}
		fail := func() {
			b, _ := json.Marshal(cachedImage{})
			m.cache.Set(key, b, imageFailTTL)
		}

		// Try the URL as given, then the same content through a gateway that is
		// actually up. An IPFS image is addressed by its CID, so the host in the
		// URL is only one of many doors to identical bytes — and collections
		// routinely name a door that has since closed. Trenchors' images point
		// at w3s.link, which answers 504 for every one of them.
		resp, err := client.Get(raw)
		ok := err == nil && resp.StatusCode == http.StatusOK &&
			strings.HasPrefix(resp.Header.Get("Content-Type"), "image/")
		if !ok {
			if resp != nil {
				resp.Body.Close()
			}
			if alt := ipfsFallbackURL(raw); alt != "" {
				resp, err = client.Get(alt)
				ok = err == nil && resp.StatusCode == http.StatusOK &&
					strings.HasPrefix(resp.Header.Get("Content-Type"), "image/")
			}
		}
		if !ok {
			if resp != nil {
				resp.Body.Close()
			}
			fail()
			return
		}
		defer resp.Body.Close()
		ct := resp.Header.Get("Content-Type")
		body, err := io.ReadAll(io.LimitReader(resp.Body, imageMaxBytes+1))
		if err != nil || len(body) == 0 || len(body) > imageMaxBytes {
			fail()
			return
		}
		if small, sct, ok := downscale(body, size); ok {
			body, ct = small, sct
		}
		if b, e := json.Marshal(cachedImage{ContentType: ct, Body: body}); e == nil {
			m.cache.Set(key, b, imageCacheTTL)
		}
	}()
	return ch
}

// downscale re-encodes a logo at icon size. The GILTS logo is a 621 KB PNG
// drawn into a 28px circle; at 96px it is a few KB and looks identical, which
// is the difference between an icon that appears and one that doesn't.
//
// Returns ok=false for anything it can't decode (SVG, WebP, AVIF) — those are
// cached as-is, since a format we can't read is not one we should mangle.
func downscale(body []byte, targetPx int) ([]byte, string, bool) {
	src, _, err := image.Decode(bytes.NewReader(body))
	if err != nil {
		return nil, "", false
	}
	b := src.Bounds()
	if b.Dx() <= targetPx && b.Dy() <= targetPx {
		return nil, "", false // already small; re-encoding would only lose quality
	}
	w, h := b.Dx(), b.Dy()
	if w > h {
		h = h * targetPx / w
		w = targetPx
	} else {
		w = w * targetPx / h
		h = targetPx
	}
	if w < 1 || h < 1 {
		return nil, "", false
	}
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	xdraw.CatmullRom.Scale(dst, dst.Bounds(), src, b, draw.Over, nil)

	// Transparency decides the format. Token logos are cut-outs and need PNG;
	// NFT art is usually a full-bleed picture, and PNG is the wrong codec for
	// one — a 320px Mad Lad is 113 KB as PNG and about a fifth of that as
	// JPEG, for a difference nobody can see at tile size.
	var out bytes.Buffer
	if hasTransparency(dst) {
		if png.Encode(&out, dst) != nil || out.Len() >= len(body) {
			return nil, "", false
		}
		return out.Bytes(), "image/png", true
	}
	if jpeg.Encode(&out, dst, &jpeg.Options{Quality: 82}) != nil || out.Len() >= len(body) {
		return nil, "", false
	}
	return out.Bytes(), "image/jpeg", true
}

// hasTransparency reports whether any pixel is not fully opaque. Sampled on a
// grid rather than every pixel: a logo with a transparent background has it
// along the edges, and a photograph has none anywhere.
func hasTransparency(img *image.RGBA) bool {
	b := img.Bounds()
	stepX := max(1, b.Dx()/64)
	stepY := max(1, b.Dy()/64)
	for y := b.Min.Y; y < b.Max.Y; y += stepY {
		for x := b.Min.X; x < b.Max.X; x += stepX {
			if img.RGBAAt(x, y).A < 250 {
				return true
			}
		}
	}
	return false
}

func hashURL(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:16])
}

// isPrivateHost reports whether a hostname resolves somewhere we must never
// fetch from — loopback, link-local, or private ranges. A literal IP is checked
// directly; a name is resolved, because "internal.example.com" pointing at
// 10.0.0.1 is the whole trick.
func isPrivateHost(host string) bool {
	if host == "" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return isPrivateIP(ip)
	}
	ips, err := net.LookupIP(host)
	if err != nil || len(ips) == 0 {
		return true
	}
	for _, ip := range ips {
		if isPrivateIP(ip) {
			return true
		}
	}
	return false
}

func isPrivateIP(ip net.IP) bool {
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
		ip.IsLinkLocalMulticast() || ip.IsUnspecified() ||
		ip.Equal(net.IPv4bcast)
}

// ipfsFallbackURL rewrites an IPFS URL to a gateway we know answers.
//
// Two shapes are in the wild: the subdomain form <cid>.ipfs.<host>/<path> and
// the path form <host>/ipfs/<cid>/<path>. Both carry the CID, which is the
// only part that identifies the content — the host is just whoever agreed to
// serve it, and those agreements lapse. Returns "" when the URL is not IPFS or
// is already pointed at the fallback.
func ipfsFallbackURL(raw string) string {
	const fallbackHost = "https://ipfs.io/ipfs/"
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return ""
	}
	if strings.HasPrefix(raw, fallbackHost) {
		return ""
	}
	// Subdomain form: the CID is the first label, "ipfs" the second.
	labels := strings.Split(u.Hostname(), ".")
	if len(labels) >= 3 && labels[1] == "ipfs" {
		return fallbackHost + labels[0] + u.EscapedPath()
	}
	// Path form.
	if idx := strings.Index(u.EscapedPath(), "/ipfs/"); idx >= 0 {
		return fallbackHost + strings.TrimPrefix(u.EscapedPath()[idx+len("/ipfs/"):], "/")
	}
	return ""
}
