#!/usr/bin/env bash
set -euo pipefail

FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
MINIO_PORT="${MINIO_PORT:-9000}"

print_env_help() {
  cat <<EOF

After the tunnels are running, copy the public HTTPS URLs into .env:

NEXT_PUBLIC_API_URL=https://<backend-tunnel>
CORS_ORIGINS=http://localhost:3000,https://<frontend-tunnel>

Optional, only if people outside your machine need to upload PPT/PPTX files:
MINIO_PUBLIC_ENDPOINT=<minio-tunnel-host-without-https>

Then restart the services that read those values:
docker compose up -d --force-recreate api worker web

EOF
}

print_manual_commands() {
  if [ "$1" = "ngrok" ]; then
    cat <<EOF
Open separate terminal tabs and run:

Frontend:
  ngrok http ${FRONTEND_PORT}

Backend:
  ngrok http ${BACKEND_PORT}

Optional MinIO upload endpoint:
  ngrok http ${MINIO_PORT}

Copy the public HTTPS forwarding URLs from the tunnel output.
EOF
    return
  fi

  cat <<EOF
Open separate terminal tabs and run:

Frontend:
  cloudflared tunnel --url http://localhost:${FRONTEND_PORT}

Backend:
  cloudflared tunnel --url http://localhost:${BACKEND_PORT}

Optional MinIO upload endpoint:
  cloudflared tunnel --url http://localhost:${MINIO_PORT}

Copy the public HTTPS URLs from the tunnel output.
EOF
}

if command -v ngrok >/dev/null 2>&1; then
  cat <<EOF
ngrok is installed. Use it for local public preview.

Local frontend: http://localhost:${FRONTEND_PORT}
Local backend:  http://localhost:${BACKEND_PORT}
EOF
  print_manual_commands "ngrok"
  print_env_help
  exit 0
fi

if command -v cloudflared >/dev/null 2>&1; then
  cat <<EOF
ngrok is not installed, but cloudflared is available.
EOF
  print_manual_commands "cloudflared"
  print_env_help
  exit 0
fi

cat <<EOF
Neither ngrok nor cloudflared was found.

Install one of them, then rerun this script:
- ngrok: https://ngrok.com/download
- cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

Manual ngrok commands after installing:
  ngrok http ${FRONTEND_PORT}
  ngrok http ${BACKEND_PORT}

Manual cloudflared commands after installing:
  cloudflared tunnel --url http://localhost:${FRONTEND_PORT}
  cloudflared tunnel --url http://localhost:${BACKEND_PORT}
EOF
print_env_help
