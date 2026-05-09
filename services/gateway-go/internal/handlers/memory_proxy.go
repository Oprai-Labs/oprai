package handlers

import (
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/oprai/oprai/services/gateway-go/internal/middleware"
)

// MemoryProxy reverse-proxies REST requests to the memory service's HTTP server.
type MemoryProxy struct {
	proxy          *httputil.ReverseProxy
	internalAPIKey string
}

// NewMemoryProxy creates a new MemoryProxy that forwards requests to memoryServiceURL.
func NewMemoryProxy(memoryServiceURL string, internalAPIKey string) *MemoryProxy {
	target, err := url.Parse(memoryServiceURL)
	if err != nil {
		slog.Error("invalid memory service URL", "url", memoryServiceURL, "error", err)
		target, _ = url.Parse("http://localhost:3040")
	}

	rp := httputil.NewSingleHostReverseProxy(target)

	originalDirector := rp.Director
	rp.Director = func(req *http.Request) {
		originalDirector(req)
		req.Header.Del("X-User-Wallet")
		if wallet := middleware.GetWallet(req.Context()); wallet != "" {
			req.Header.Set("X-User-Wallet", wallet)
		}
		req.Header.Set("X-Internal-Api-Key", internalAPIKey)
		if reqID := chimiddleware.GetReqID(req.Context()); reqID != "" {
			req.Header.Set("X-Request-ID", reqID)
		}
	}

	rp.ModifyResponse = func(resp *http.Response) error {
		resp.Header.Del("Access-Control-Allow-Origin")
		resp.Header.Del("Access-Control-Allow-Methods")
		resp.Header.Del("Access-Control-Allow-Headers")
		resp.Header.Del("Access-Control-Allow-Credentials")
		resp.Header.Del("Access-Control-Expose-Headers")
		resp.Header.Del("Access-Control-Max-Age")
		return nil
	}

	rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		slog.Error("memory proxy error", "error", err, "path", r.URL.Path)
		writeError(w, http.StatusBadGateway, "Memory service unavailable")
	}

	return &MemoryProxy{proxy: rp, internalAPIKey: internalAPIKey}
}

func (p *MemoryProxy) GetMemories(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/memory"
	p.proxy.ServeHTTP(w, r)
}

func (p *MemoryProxy) ClearMemories(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/memory"
	p.proxy.ServeHTTP(w, r)
}

func (p *MemoryProxy) GetConsent(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/consent"
	p.proxy.ServeHTTP(w, r)
}

func (p *MemoryProxy) UpdateConsent(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/consent"
	p.proxy.ServeHTTP(w, r)
}

func (p *MemoryProxy) Summarize(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/summarize"
	p.proxy.ServeHTTP(w, r)
}

func (p *MemoryProxy) SearchMemories(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/memory/search"
	p.proxy.ServeHTTP(w, r)
}

func (p *MemoryProxy) DeleteMemoryById(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = fmt.Sprintf("/memory/%s", id)
	p.proxy.ServeHTTP(w, r)
}

func (p *MemoryProxy) StoreMemory(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/memory"
	p.proxy.ServeHTTP(w, r)
}
