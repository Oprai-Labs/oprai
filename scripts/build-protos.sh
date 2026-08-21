#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build-protos.sh  --  Generate gRPC stubs for Go, Python, and Rust services
#
# Usage:
#   ./scripts/build-protos.sh          # generate all
#   ./scripts/build-protos.sh go       # Go services only
#   ./scripts/build-protos.sh python   # Python services only
#   ./scripts/build-protos.sh rust     # Rust validation only
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_DIR="$REPO_ROOT/proto"

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

ok()   { echo -e "  ${GREEN}[OK]${NC}   $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; }
info() { echo -e "  ${CYAN}[INFO]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; }

SUCCESSES=()
FAILURES=()

record_success() { SUCCESSES+=("$1"); }
record_failure()  { FAILURES+=("$1"); }

# ── Preflight checks ───────────────────────────────────────────────────────

echo ""
echo "================================================="
echo "  OPRAI Proto Build Script"
echo "================================================="
echo ""

# Check protoc is installed
if ! command -v protoc &>/dev/null; then
  fail "protoc is not installed."
  echo ""
  echo "  Install Protocol Buffers compiler:"
  echo "    macOS:  brew install protobuf"
  echo "    Ubuntu: sudo apt install -y protobuf-compiler"
  echo "    Or:     https://github.com/protocolbuffers/protobuf/releases"
  echo ""
  exit 1
fi
ok "protoc found: $(protoc --version)"

# Discover all .proto files
PROTO_FILES=()
while IFS= read -r -d '' f; do
  PROTO_FILES+=("$f")
done < <(find "$PROTO_DIR" -name '*.proto' -print0 | sort -z)

if [[ ${#PROTO_FILES[@]} -eq 0 ]]; then
  fail "No .proto files found in $PROTO_DIR"
  exit 1
fi
ok "Found ${#PROTO_FILES[@]} proto file(s):"
for f in "${PROTO_FILES[@]}"; do
  info "  $(realpath --relative-to="$REPO_ROOT" "$f" 2>/dev/null || echo "$f")"
done
echo ""

# ── Go generation ───────────────────────────────────────────────────────────

GO_SERVICES=("auth-service-go" "admin-service-go" "gateway-go")

generate_go() {
  echo "── Go Code Generation ──────────────────────────"
  echo ""

  # Check Go protoc plugins
  local go_ok=true
  if ! command -v protoc-gen-go &>/dev/null; then
    fail "protoc-gen-go not found. Install: go install google.golang.org/protobuf/cmd/protoc-gen-go@latest"
    go_ok=false
  fi
  if ! command -v protoc-gen-go-grpc &>/dev/null; then
    fail "protoc-gen-go-grpc not found. Install: go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest"
    go_ok=false
  fi

  if [[ "$go_ok" == "false" ]]; then
    for svc in "${GO_SERVICES[@]}"; do
      record_failure "go:$svc (missing plugins)"
    done
    return
  fi

  ok "protoc-gen-go found"
  ok "protoc-gen-go-grpc found"
  echo ""

  for svc in "${GO_SERVICES[@]}"; do
    local out_dir="$REPO_ROOT/services/$svc/proto/gen/go"
    info "Generating Go stubs for $svc -> $out_dir"

    mkdir -p "$out_dir"

    # Each service vendors its own copy of the stubs, so the import path baked
    # into them must be THAT service's module — not the `go_package` written in
    # the .proto, which names a module nothing provides. Without these M
    # overrides the files compile in isolation but `go vet ./...` and
    # `go test ./...` fail on every service (only `./cmd/...` builds, which is
    # why the binaries still shipped while CI was red).
    local svc_module
    svc_module="$(awk '/^module /{print $2; exit}' "$REPO_ROOT/services/$svc/go.mod")"
    local gen_module="$svc_module/proto/gen/go"

    local m_opts=()
    for pf in "${PROTO_FILES[@]}"; do
      local rel pkg
      rel="${pf#$REPO_ROOT/}"
      pkg="$(sed -nE 's|.*go_package[[:space:]]*=[[:space:]]*".*/([A-Za-z0-9_]+)".*|\1|p' "$pf" | head -1)"
      [[ -z "$pkg" ]] && { fail "$rel: no go_package option"; continue; }
      m_opts+=("--go_opt=M$rel=$gen_module/$pkg" "--go-grpc_opt=M$rel=$gen_module/$pkg")
    done

    if protoc \
      --proto_path="$REPO_ROOT" \
      --go_out="$out_dir" \
      --go_opt=module="$gen_module" \
      --go-grpc_out="$out_dir" \
      --go-grpc_opt=module="$gen_module" \
      "${m_opts[@]}" \
      "${PROTO_FILES[@]}"; then
      ok "$svc: Go stubs generated"
      record_success "go:$svc"
    else
      fail "$svc: Go code generation failed"
      record_failure "go:$svc"
    fi
  done
  echo ""
}

# ── Python generation ───────────────────────────────────────────────────────

PY_SERVICES=("chat-service-py" "memory-service-py")

generate_python() {
  echo "── Python Code Generation ──────────────────────"
  echo ""

  # Check grpcio-tools
  if ! python3 -c "import grpc_tools.protoc" &>/dev/null; then
    fail "grpcio-tools not found. Install: pip install grpcio-tools"
    for svc in "${PY_SERVICES[@]}"; do
      record_failure "python:$svc (missing grpcio-tools)"
    done
    return
  fi
  ok "grpcio-tools found"
  echo ""

  for svc in "${PY_SERVICES[@]}"; do
    local out_dir="$REPO_ROOT/services/$svc/proto_gen"
    info "Generating Python stubs for $svc -> $out_dir"

    mkdir -p "$out_dir"

    if python3 -m grpc_tools.protoc \
      --proto_path="$REPO_ROOT" \
      --python_out="$out_dir" \
      --grpc_python_out="$out_dir" \
      --pyi_out="$out_dir" \
      "${PROTO_FILES[@]}"; then

      # Create __init__.py files so the generated packages are importable
      find "$out_dir" -type d -exec touch {}/__init__.py \;

      ok "$svc: Python stubs generated"
      record_success "python:$svc"
    else
      fail "$svc: Python code generation failed"
      record_failure "python:$svc"
    fi
  done
  echo ""
}

# ── Rust validation ─────────────────────────────────────────────────────────

generate_rust() {
  echo "── Rust Proto Validation ───────────────────────"
  echo ""

  local rs_svc="solana-service-rs"
  local build_rs="$REPO_ROOT/services/$rs_svc/build.rs"

  info "Rust uses tonic-build in build.rs -- validating proto files exist"

  # Check that all proto files referenced are present
  local all_exist=true
  for f in "${PROTO_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
      fail "Missing proto file: $f"
      all_exist=false
    fi
  done

  if [[ "$all_exist" == "true" ]]; then
    ok "All proto files present for $rs_svc (tonic-build will generate at cargo build time)"

    if [[ -f "$build_rs" ]]; then
      ok "build.rs exists at $build_rs"
    else
      warn "build.rs not found at $build_rs -- create it to enable tonic-build"
    fi

    record_success "rust:$rs_svc"
  else
    record_failure "rust:$rs_svc"
  fi
  echo ""
}

# ── Main ────────────────────────────────────────────────────────────────────

TARGET="${1:-all}"

case "$TARGET" in
  go)
    generate_go
    ;;
  python|py)
    generate_python
    ;;
  rust|rs)
    generate_rust
    ;;
  all)
    generate_go
    generate_python
    generate_rust
    ;;
  *)
    echo "Usage: $0 [all|go|python|rust]"
    exit 1
    ;;
esac

# ── Summary ─────────────────────────────────────────────────────────────────

echo "================================================="
echo "  Build Summary"
echo "================================================="
echo ""

if [[ ${#SUCCESSES[@]} -gt 0 ]]; then
  echo -e "  ${GREEN}Succeeded (${#SUCCESSES[@]}):${NC}"
  for s in "${SUCCESSES[@]}"; do
    echo -e "    ${GREEN}+${NC} $s"
  done
fi

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo ""
  echo -e "  ${RED}Failed (${#FAILURES[@]}):${NC}"
  for f in "${FAILURES[@]}"; do
    echo -e "    ${RED}x${NC} $f"
  done
fi

echo ""

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo -e "  ${RED}Some targets failed.${NC}"
  exit 1
else
  echo -e "  ${GREEN}All targets succeeded.${NC}"
  exit 0
fi
