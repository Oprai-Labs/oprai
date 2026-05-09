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
	contentType := header.Header.Get("Content-Type")
	filename := strings.ToLower(header.Filename)
	ext := ""
	if contentType == "" {
		switch {
		case strings.HasSuffix(filename, ".png"):
			contentType, ext = "image/png", ".png"
		case strings.HasSuffix(filename, ".jpg") || strings.HasSuffix(filename, ".jpeg"):
			contentType, ext = "image/jpeg", ".jpg"
		case strings.HasSuffix(filename, ".gif"):
			contentType, ext = "image/gif", ".gif"
		case strings.HasSuffix(filename, ".webp"):
			contentType, ext = "image/webp", ".webp"
		case strings.HasSuffix(filename, ".mp4"):
			contentType, ext = "video/mp4", ".mp4"
		default:
			writeError(w, http.StatusBadRequest, "Unsupported file type. Use .jpg, .png, .gif, .webp, or .mp4")
			return
		}
	} else {
		ext = extFromContentType(contentType, filename)
	}

	isVideo := strings.HasPrefix(contentType, "video/")
	isImage := strings.HasPrefix(contentType, "image/")
	if !isImage && !isVideo {
		writeError(w, http.StatusBadRequest, "Only image or video files are allowed")
		return
	}

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

// ─── Helpers ──────────────────────────────────────────────────────────────────

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		// Fallback: use timestamp (should never happen in practice)
		return hex.EncodeToString([]byte(strings.ReplaceAll(time.Now().String(), " ", "")))
	}
	return hex.EncodeToString(b)
}

func extFromContentType(ct, filename string) string {
	switch {
	case strings.Contains(ct, "png"):
		return ".png"
	case strings.Contains(ct, "gif"):
		return ".gif"
	case strings.Contains(ct, "webp"):
		return ".webp"
	case strings.Contains(ct, "jpeg"), strings.Contains(ct, "jpg"):
		return ".jpg"
	case strings.Contains(ct, "mp4"):
		return ".mp4"
	}
	// Fall back to filename extension
	if i := strings.LastIndex(filename, "."); i >= 0 {
		return filename[i:]
	}
	return ".bin"
}
