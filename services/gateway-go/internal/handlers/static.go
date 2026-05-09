package handlers

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// StaticHandler serves uploaded files from the local upload directory.
type StaticHandler struct {
	uploadDir     string
	publicBaseURL string
}

// NewStaticHandler creates a StaticHandler for the given upload directory.
func NewStaticHandler(uploadDir, publicBaseURL string) *StaticHandler {
	return &StaticHandler{
		uploadDir:     uploadDir,
		publicBaseURL: publicBaseURL,
	}
}

// ServeFile handles GET /uploads/* — serves files from the upload directory.
// Only allows access to files inside uploadDir (path traversal protection).
func (s *StaticHandler) ServeFile(w http.ResponseWriter, r *http.Request) {
	urlPath := strings.TrimPrefix(r.URL.Path, "/uploads/")
	urlPath = filepath.Clean(urlPath)

	absUploadDir, err := filepath.Abs(s.uploadDir)
	if err != nil {
		http.Error(w, "internal", http.StatusInternalServerError)
		return
	}

	requestedPath := filepath.Join(absUploadDir, urlPath)
	absRequested, err := filepath.Abs(requestedPath)
	if err != nil || !strings.HasPrefix(absRequested, absUploadDir+string(os.PathSeparator)) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	http.ServeFile(w, r, absRequested)
}

// ServeMetadata handles GET /m/* — short-URL route for token metadata JSON files.
// Files are stored in uploadDir/metadata/ but served at /m/<filename> to keep
// the on-chain URI short enough to fit in a Solana legacy transaction (≤1232 bytes).
func (s *StaticHandler) ServeMetadata(w http.ResponseWriter, r *http.Request) {
	filename := strings.TrimPrefix(r.URL.Path, "/m/")
	filename = filepath.Base(filepath.Clean(filename))

	if filename == "." || filename == "/" || strings.HasPrefix(filename, ".") {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	absUploadDir, err := filepath.Abs(s.uploadDir)
	if err != nil {
		http.Error(w, "internal", http.StatusInternalServerError)
		return
	}

	absPath := filepath.Join(absUploadDir, "metadata", filename)
	if !strings.HasPrefix(absPath, absUploadDir+string(os.PathSeparator)) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	http.ServeFile(w, r, absPath)
}
