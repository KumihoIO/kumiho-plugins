#!/usr/bin/env bash

set -euo pipefail

NO_START=0

for arg in "$@"; do
  case "$arg" in
    --no-start|-n)
      NO_START=1
      ;;
    -h|--help)
      cat <<'EOF'
Usage: run-local-n8n.sh [--no-start]

Builds the Kumiho n8n nodes package, installs it into ~/.n8n/custom, and (by default) starts n8n.

Options:
  --no-start, -n   Only build + install; do not start n8n
  --help, -h       Show this help
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

echo "Cleaning dist folder..."
rm -rf "dist"

if [[ ! -x "node_modules/.bin/n8n-node" ]]; then
  echo "Installing dependencies (missing n8n-node)..."
  npm install
fi

echo "Building nodes..."
npm run build

echo "Copying icons..."
# Format: SourceFile:DestinationDirName
ICON_MAP=(
  "Project.png:KumihoProject"
  "Space.png:KumihoSpace"
  "Graph.png:KumihoGraph"
  "Revision.png:KumihoRevision"
  "Item.png:KumihoItem"
  "Artifact.png:KumihoArtifact"
  "Bundle.png:KumihoBundle"
  "Search.png:KumihoSearch"
  "ResolveKref.png:KumihoResolveKref"
  "EventStream.png:KumihoEventStream"
)

for entry in "${ICON_MAP[@]}"; do
  icon="${entry%%:*}"
  destName="${entry##*:}"

  source="$PROJECT_ROOT/nodes/images/$icon"
  destDir="$PROJECT_ROOT/dist/nodes/$destName"
  dest="$destDir/$icon"

  if [[ -f "$source" ]]; then
    if [[ -d "$destDir" ]]; then
      cp -f "$source" "$dest"
      echo "Copied $icon to $destDir"
    else
      echo "Destination directory $destDir does not exist." >&2
    fi
  else
    echo "Icon $source not found." >&2
  fi
done

CUSTOM_DIR="$HOME/.n8n/custom"
mkdir -p "$CUSTOM_DIR"
cd "$CUSTOM_DIR"

if [[ ! -f "package.json" ]]; then
  echo "Initializing custom directory..."
  npm init -y >/dev/null
fi

# Remove any existing install in the custom directory
if [[ -d "node_modules/n8n-nodes-kumiho" ]]; then
  echo "Removing existing package from custom directory..."
  npm uninstall n8n-nodes-kumiho || true
fi

# Also clean up the official nodes directory where UI-installed nodes go
OFFICIAL_NODES_DIR="$HOME/.n8n/nodes"
if [[ -d "$OFFICIAL_NODES_DIR/node_modules/n8n-nodes-kumiho" ]]; then
  echo "Removing duplicate from official nodes directory..."
  rm -rf "$OFFICIAL_NODES_DIR/node_modules/n8n-nodes-kumiho"
fi

echo "Packing and installing..."
# 1) Create a tarball of the project (avoids symlink issues)
TARBALL="$(npm pack "$PROJECT_ROOT" | tail -n 1)"

if [[ ! -f "$TARBALL" ]]; then
  echo "Failed to create tarball: $TARBALL" >&2
  exit 1
fi

# 2) Install from the tarball
npm install "$TARBALL" --omit=peer

# 3) Clean up tarball
rm -f "$TARBALL"

# 4) Remove n8n-workflow to avoid instance conflicts (peer dependency issue)
if [[ -d "node_modules/n8n-workflow" ]]; then
  echo "Removing local n8n-workflow to prevent conflicts..."
  rm -rf "node_modules/n8n-workflow"
fi

# Verify installation
INSTALLED_DIST="node_modules/n8n-nodes-kumiho/dist"
if [[ ! -d "$INSTALLED_DIST" ]]; then
  echo "INSTALLED PACKAGE IS MISSING DIST FOLDER!" >&2
  ls -la "node_modules/n8n-nodes-kumiho" || true
else
  echo "Verified dist folder exists in installed package."
fi

if [[ "$NO_START" -eq 0 ]]; then
  echo "Starting n8n..."
  echo "IMPORTANT: If nodes don't show up, please HARD REFRESH your browser (Cmd+Shift+R)"

  export N8N_LOG_LEVEL='debug'
  export N8N_LOG_OUTPUT='console'

  # Try letting n8n discover it naturally in the custom folder first.
  export N8N_CUSTOM_EXTENSIONS="$CUSTOM_DIR/node_modules"
  echo "N8N_CUSTOM_EXTENSIONS: $N8N_CUSTOM_EXTENSIONS"

  # Allow access to local n8n files and user workspace (semicolon-delimited)
  export N8N_RESTRICT_FILE_ACCESS_TO="$HOME/.n8n-files;/Users/youngbin.park"
  echo "N8N_RESTRICT_FILE_ACCESS_TO: $N8N_RESTRICT_FILE_ACCESS_TO"

  # Ensure local log root exists for Write Binary File node
  LOG_ROOT="/Users/youngbin.park/n8n/kumiho-chat-logs"
  mkdir -p "$LOG_ROOT"
  if [[ ! -w "$LOG_ROOT" ]]; then
    echo "Log root is not writable: $LOG_ROOT" >&2
  fi

  # HTTPS/Tunnel configuration for webhook triggers (e.g., Telegram)
  # Option 1: Use a tunnel service (recommended for development)
  #   Start a tunnel in another terminal: ngrok http 5678
  #   Then set WEBHOOK_URL to the ngrok HTTPS URL
  # Option 2: Use cloudflared tunnel
  #   cloudflared tunnel --url http://localhost:5678
  #
  # Uncomment and set WEBHOOK_URL to your tunnel's HTTPS URL:
  # export WEBHOOK_URL="https://your-tunnel-url.ngrok-free.app"

  # For SSL with local certificates (alternative to tunnels):
  # export N8N_PROTOCOL="https"
  # export N8N_SSL_KEY="$HOME/.n8n/ssl/key.pem"
  # export N8N_SSL_CERT="$HOME/.n8n/ssl/cert.pem"

  npx -y n8n@latest
fi
