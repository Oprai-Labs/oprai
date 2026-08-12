package handlers

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// UploadHandler handles local file uploads.
type UploadHandler struct {
	uploadDir     string // absolute path to upload directory
	publicBaseURL string // public-facing base URL, e.g. https://api.oprai.xyz
	client        *http.Client
}

// NewUploadHandler creates a new UploadHandler.
// uploadDir is created on first use if it does not exist.
func NewUploadHandler(uploadDir, publicBaseURL string) *UploadHandler {
	return &UploadHandler{
		uploadDir:     uploadDir,
		publicBaseURL: strings.TrimRight(publicBaseURL, "/"),
		client:        &http.Client{Timeout: 30 * time.Second},
	}
}

// CheckUploadDirWritable reports at startup whether uploads can actually be
// stored, naming the absolute path either way.
//
// Uploaded files back permanent on-chain metadata URIs, so this is not a
// cosmetic feature that can afford to fail quietly: a token minted while
// storage is broken is broken forever. It logs rather than exits — the rest
// of the gateway is unaffected, and taking the whole API down would be a
// worse outcome than a loud line at boot.
func CheckUploadDirWritable(uploadDir string) {
	abs, err := filepath.Abs(uploadDir)
	if err != nil {
		abs = uploadDir
	}
	probe := filepath.Join(abs, ".writable")
	if err := os.MkdirAll(abs, 0o755); err != nil {
		slog.Error("Upload storage is NOT writable — token launches will be refused",
			"dir", abs, "error", err)
		return
	}
	if err := os.WriteFile(probe, []byte("ok"), 0o644); err != nil {
		slog.Error("Upload storage is NOT writable — token launches will be refused",
			"dir", abs, "error", err)
		return
	}
	_ = os.Remove(probe)
	slog.Info("Upload storage ready", "dir", abs)
}

// UploadResponse is the JSON body returned to the client after a successful upload.
type UploadResponse struct {
	URL      string `json:"url"`      // Public HTTP URL
	Filename string `json:"filename"` // Generated filename (uuid.ext)
	// ipfsHash retained for backward-compat with frontend code that reads this field;
	// it is set to an empty string since we no longer use IPFS.
	IPFSHash string `json:"ipfsHash"`
}

// MetadataUploadRequest is the JSON body for POST /upload/metadata.
type MetadataUploadRequest struct {
	Name        string `json:"name"`
	Symbol      string `json:"symbol"`
	Description string `json:"description"`
	Image       string `json:"image"`        // public HTTP URL of the token image
	Twitter     string `json:"twitter,omitempty"`
	Telegram    string `json:"telegram,omitempty"`
	Website     string `json:"website,omitempty"`
	Banner      string `json:"banner,omitempty"`
	ShowName    bool   `json:"showName"`
	// createdOn is set server-side to "https://pump.fun" so their indexer recognises the token
	CreatedOn   string `json:"createdOn,omitempty"`
}

// MetadataUploadResponse is returned from POST /upload/metadata.
type MetadataUploadResponse struct {
	URL      string `json:"url"`      // Public HTTP URL of the metadata JSON
	Filename string `json:"filename"` // e.g. abc123.json
}

// UploadImage handles POST /upload/image.
// Accepts multipart/form-data with a "file" field.
// Stores the file locally and returns its public HTTP URL.
func (u *UploadHandler) UploadImage(w http.ResponseWriter, r *http.Request) {
	const maxImageSize = 15 << 20 // 15 MB
	const maxVideoSize = 30 << 20 // 30 MB

	if err := r.ParseMultipartForm(32 << 20); err != nil {
		writeError(w, http.StatusBadRequest, "Failed to parse multipart form")
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		writeError(w, http.StatusBadRequest, "No file provided or invalid form field")
		return
	}
	defer file.Close()

	// ── Determine & validate content type ─────────────────────────────────────
	// Resolve to one of a small allowlist of concrete, non-scriptable media
	// types. The stored extension is NEVER taken from the raw client filename:
	// an image/svg+xml (or any unrecognised image/*) used to fall through to the
	// filename's own extension, letting a caller store <random>.svg or
	// <random>.html and have it served back inline as active content — a stored
	// XSS on our own domain. svg is deliberately excluded; only png/jpg/gif/webp/mp4.
	contentType, ext, ok := safeMediaType(header.Header.Get("Content-Type"), header.Filename)
	if !ok {
		writeError(w, http.StatusBadRequest, "Unsupported file type. Use .jpg, .png, .gif, .webp, or .mp4")
		return
	}
	isVideo := contentType == "video/mp4"

	maxSize := int64(maxImageSize)
	if isVideo {
		maxSize = maxVideoSize
	}
	if header.Size > maxSize {
		limit := "15MB"
		if isVideo {
			limit = "30MB"
		}
		writeError(w, http.StatusBadRequest, "File size exceeds "+limit+" limit")
		return
	}

	// ── Store file on disk ─────────────────────────────────────────────────────
	dir := filepath.Join(u.uploadDir, "img")
	if err := os.MkdirAll(dir, 0755); err != nil {
		slog.Error("Failed to create upload directory", "error", err)
		writeError(w, http.StatusInternalServerError, "Storage not available")
		return
	}

	generatedName := randomHex(16) + ext
	destPath := filepath.Join(dir, generatedName)
	dest, err := os.Create(destPath)
	if err != nil {
		slog.Error("Failed to create file", "path", destPath, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to store file")
		return
	}
	defer dest.Close()

	if _, err = io.Copy(dest, io.LimitReader(file, maxSize+1)); err != nil {
		slog.Error("Failed to write file", "path", destPath, "error", err)
		os.Remove(destPath)
		writeError(w, http.StatusInternalServerError, "Failed to store file")
		return
	}

	publicURL := u.publicBaseURL + "/uploads/img/" + generatedName
	slog.Info("Image uploaded", "filename", generatedName, "url", publicURL)

	writeJSON(w, http.StatusOK, UploadResponse{
		URL:      publicURL,
		Filename: generatedName,
		IPFSHash: "",
	})
}

// UploadMetadata handles POST /upload/metadata.
// Accepts JSON with token metadata fields, stores a metadata JSON file locally,
// and returns its public HTTP URL to use as the pump.fun token URI.
func (u *UploadHandler) UploadMetadata(w http.ResponseWriter, r *http.Request) {
	var req MetadataUploadRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid JSON body")
		return
	}

	if req.Name == "" || req.Symbol == "" {
		writeError(w, http.StatusBadRequest, "name and symbol are required")
		return
	}
	if req.Image == "" {
		writeError(w, http.StatusBadRequest, "image URL is required")
		return
	}
	if !strings.HasPrefix(req.Image, "http://") && !strings.HasPrefix(req.Image, "https://") {
		writeError(w, http.StatusBadRequest, "image must be an HTTP/HTTPS URL")
		return
	}

	// Normalise symbol to uppercase; stamp createdOn for pump.fun indexer
	req.Symbol = strings.ToUpper(req.Symbol)
	req.ShowName = true
	req.CreatedOn = "https://pump.fun"

	dir := filepath.Join(u.uploadDir, "metadata")
	if err := os.MkdirAll(dir, 0755); err != nil {
		slog.Error("Failed to create metadata directory", "error", err)
		writeError(w, http.StatusInternalServerError, "Storage not available")
		return
	}

	// 5 random bytes → 10-char hex filename.
	// Short enough that the full URL (https://api.oprai.xyz/m/<name>.json = 39 chars)
	// fits inside a Solana legacy transaction even for max-length token names and symbols.
	generatedName := randomHex(5) + ".json"
	destPath := filepath.Join(dir, generatedName)

	data, err := json.MarshalIndent(req, "", "  ")
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to build metadata JSON")
		return
	}
	if err := os.WriteFile(destPath, data, 0644); err != nil {
		slog.Error("Failed to write metadata file", "path", destPath, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to store metadata")
		return
	}

	// Serve via /m/ (short alias) — same file, shorter URL for on-chain storage.
	publicURL := u.publicBaseURL + "/m/" + generatedName
	slog.Info("Metadata uploaded", "filename", generatedName, "url", publicURL)

	writeJSON(w, http.StatusOK, MetadataUploadResponse{
		URL:      publicURL,
		Filename: generatedName,
	})
}

// UploadToPumpFunIPFS handles POST /upload/pumpfun-ipfs.
//
// It forwards a multipart form to pump.fun's own IPFS endpoint and returns
// their JSON verbatim. The frontend used to call `https://pump.fun/api/ipfs`
// straight from the browser, where it fails on CORS every time — silently, in
// a `catch` that logged a warning and moved on to the next fallback. Server
// to server the same request answers 200, so the preferred path was never
// actually available and every launch was running on fallbacks.
//
// Hosting the metadata on pump.fun's own IPFS is the surest way for their
// indexer to resolve a new token, which makes this the primary route and not
// a nicety.
func (u *UploadHandler) UploadToPumpFunIPFS(w http.ResponseWriter, r *http.Request) {
	const maxUpload = 30 << 20 // matches the image/video ceiling above

	contentType := r.Header.Get("Content-Type")
	if !strings.HasPrefix(contentType, "multipart/form-data") {
		writeError(w, http.StatusBadRequest, "Expected a multipart/form-data body")
		return
	}

	// Pass the body through untouched — re-encoding the form would mean
	// re-deriving the boundary and every field for no benefit.
	body := http.MaxBytesReader(w, r.Body, maxUpload)
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, "https://pump.fun/api/ipfs", body)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Could not reach pump.fun")
		return
	}
	req.Header.Set("Content-Type", contentType)

	resp, err := u.client.Do(req)
	if err != nil {
		slog.Error("pump.fun IPFS upload failed", "error", err)
		writeError(w, http.StatusBadGateway, "Could not reach pump.fun to store the token image")
		return
	}
	defer resp.Body.Close()

	payload, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		writeError(w, http.StatusBadGateway, "pump.fun returned an unreadable response")
		return
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		slog.Error("pump.fun IPFS upload rejected", "status", resp.StatusCode, "body", string(payload[:min(len(payload), 300)]))
		writeError(w, http.StatusBadGateway, "pump.fun could not store the token image")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(payload)
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		// Fallback: use timestamp (should never happen in practice)
		return hex.EncodeToString([]byte(strings.ReplaceAll(time.Now().String(), " ", "")))
	}
	return hex.EncodeToString(b)
}

// safeMediaType maps a declared content-type (and, only as a fallback, the
// filename extension) to one of a small allowlist of concrete, non-scriptable
// media types. It returns ok=false for everything else — notably
// image/svg+xml, text/html and unknown types — so the stored file extension can
// never be attacker-chosen and later served back as active content.
func safeMediaType(ct, filename string) (contentType, ext string, ok bool) {
	ct = strings.ToLower(strings.TrimSpace(ct))
	if i := strings.IndexByte(ct, ';'); i >= 0 { // drop "; charset=…" and friends
		ct = strings.TrimSpace(ct[:i])
	}
	switch ct {
	case "image/png":
		return "image/png", ".png", true
	case "image/jpeg", "image/jpg":
		return "image/jpeg", ".jpg", true
	case "image/gif":
		return "image/gif", ".gif", true
	case "image/webp":
		return "image/webp", ".webp", true
	case "video/mp4":
		return "video/mp4", ".mp4", true
	}
	// No usable content-type — consult the filename, but against the SAME allowlist.
	filename = strings.ToLower(filename)
	switch {
	case strings.HasSuffix(filename, ".png"):
		return "image/png", ".png", true
	case strings.HasSuffix(filename, ".jpg"), strings.HasSuffix(filename, ".jpeg"):
		return "image/jpeg", ".jpg", true
	case strings.HasSuffix(filename, ".gif"):
		return "image/gif", ".gif", true
	case strings.HasSuffix(filename, ".webp"):
		return "image/webp", ".webp", true
	case strings.HasSuffix(filename, ".mp4"):
		return "video/mp4", ".mp4", true
	}
	return "", "", false
}
