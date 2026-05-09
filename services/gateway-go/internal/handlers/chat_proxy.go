package handlers

import (
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/oprai/oprai/services/gateway-go/internal/middleware"
)

// ChatProxy reverse-proxies REST requests to the chat service's HTTP server.
type ChatProxy struct {
	proxy          *httputil.ReverseProxy
	internalAPIKey string
}

// NewChatProxy creates a new ChatProxy that forwards requests to chatServiceURL.
func NewChatProxy(chatServiceURL string, internalAPIKey string) *ChatProxy {
	target, err := url.Parse(chatServiceURL)
	if err != nil {
		slog.Error("invalid chat service URL", "url", chatServiceURL, "error", err)
		target, _ = url.Parse("http://localhost:3020")
	}

	// Custom transport with explicit timeouts.
	// ResponseHeaderTimeout: time to wait for the first response header.
	// SSE streams stay alive longer via the connection itself — this only
	// covers the initial handshake, not the full streaming duration.
	transport := &http.Transport{
		ResponseHeaderTimeout: 30 * time.Second,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
	}

	rp := httputil.NewSingleHostReverseProxy(target)
	rp.Transport = transport
	rp.FlushInterval = -1 // flush SSE chunks immediately

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

	// Strip upstream CORS headers (gateway handles CORS)
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
		slog.Error("chat proxy error", "error", err, "path", r.URL.Path)
		writeError(w, http.StatusBadGateway, "Chat service unavailable")
	}

	return &ChatProxy{
		proxy:          rp,
		internalAPIKey: internalAPIKey,
	}
}

// ListSessions proxies GET /chat/sessions
func (p *ChatProxy) ListSessions(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/sessions"
	p.proxy.ServeHTTP(w, r)
}

// CreateSession proxies POST /chat/sessions
func (p *ChatProxy) CreateSession(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/sessions"
	p.proxy.ServeHTTP(w, r)
}

// GetSession proxies GET /chat/sessions/{id}
func (p *ChatProxy) GetSession(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/sessions/" + id
	p.proxy.ServeHTTP(w, r)
}

// DeleteSession proxies DELETE /chat/sessions/{id}
func (p *ChatProxy) DeleteSession(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/sessions/" + id
	p.proxy.ServeHTTP(w, r)
}

// UpdateSession proxies PATCH /chat/sessions/{id}
func (p *ChatProxy) UpdateSession(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/sessions/" + id
	p.proxy.ServeHTTP(w, r)
}

// PinSession proxies PATCH /chat/sessions/{id}/pin
func (p *ChatProxy) PinSession(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/sessions/" + id + "/pin"
	p.proxy.ServeHTTP(w, r)
}

// GetMessages proxies GET /chat/sessions/{id}/messages
func (p *ChatProxy) GetMessages(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/sessions/" + id + "/messages"
	p.proxy.ServeHTTP(w, r)
}

// SendMessage proxies POST /chat/sessions/{id}/messages
func (p *ChatProxy) SendMessage(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/sessions/" + id + "/messages"
	p.proxy.ServeHTTP(w, r)
}

// StreamMessages proxies GET /chat/sessions/{id}/messages/stream
func (p *ChatProxy) StreamMessages(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	r.URL.Path = "/sessions/" + id + "/messages/stream"
	p.proxy.ServeHTTP(w, r)
}

// PatchMessageMeta proxies PATCH /chat/sessions/{id}/messages/{msgId}/metadata
func (p *ChatProxy) PatchMessageMeta(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	msgID := chi.URLParam(r, "msgId")
	r.URL.Path = "/sessions/" + id + "/messages/" + msgID + "/metadata"
	p.proxy.ServeHTTP(w, r)
}

// PostMessageFeedback proxies POST /chat/messages/{msgId}/feedback to the
// chat-service. Body: {"rating": -1|1, "note"?: string}.
func (p *ChatProxy) PostMessageFeedback(w http.ResponseWriter, r *http.Request) {
	msgID := chi.URLParam(r, "msgId")
	r.URL.Path = "/messages/" + msgID + "/feedback"
	p.proxy.ServeHTTP(w, r)
}

// StreamMessagesPost proxies POST /chat/messages/stream
func (p *ChatProxy) StreamMessagesPost(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/messages/stream"
	p.proxy.ServeHTTP(w, r)
}

// EditMessage proxies POST /chat/messages/edit (edit-and-resend a previous user message).
func (p *ChatProxy) EditMessage(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/messages/edit"
	p.proxy.ServeHTTP(w, r)
}

// SendChat proxies POST /chat
func (p *ChatProxy) SendChat(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/chat"
	p.proxy.ServeHTTP(w, r)
}

// StreamChat proxies GET /chat/stream
func (p *ChatProxy) StreamChat(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/chat/stream"
	p.proxy.ServeHTTP(w, r)
}

// TaxReport proxies POST /tax/report
func (p *ChatProxy) TaxReport(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/tax/report"
	p.proxy.ServeHTTP(w, r)
}

// TaxExport proxies POST /tax/export
func (p *ChatProxy) TaxExport(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/tax/export"
	p.proxy.ServeHTTP(w, r)
}

// TaxEvents proxies GET /tax/events/{year}
func (p *ChatProxy) TaxEvents(w http.ResponseWriter, r *http.Request) {
	year := chi.URLParam(r, "year")
	r.URL.Path = "/tax/events/" + year
	p.proxy.ServeHTTP(w, r)
}

// TaxYears proxies GET /tax/years
func (p *ChatProxy) TaxYears(w http.ResponseWriter, r *http.Request) {
	r.URL.Path = "/tax/years"
	p.proxy.ServeHTTP(w, r)
}
