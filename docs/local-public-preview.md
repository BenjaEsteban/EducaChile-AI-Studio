# Local Public Preview

Use this only for development demos. It exposes your local Docker services through temporary public URLs.

Do not expose real API keys, private files, sensitive data, or a database you care about. Authentication is not implemented yet, and the local development seed user is active by default.

## 1. Start Docker

```bash
cp .env.example .env
docker compose up -d --build
make migrate
make seed-dev
```

The local app should be available at:

- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- MinIO API: `http://localhost:9000`

## 2. Expose Frontend and Backend

Run the helper:

```bash
scripts/expose-local.sh
```

If `ngrok` is installed, the script prints the recommended ngrok commands:

```bash
ngrok http 3000
ngrok http 8000
```

Run them in separate terminal tabs and copy the public HTTPS forwarding URLs.

If `ngrok` is not installed but `cloudflared` is available, use:

```bash
cloudflared tunnel --url http://localhost:3000
cloudflared tunnel --url http://localhost:8000
```

## 3. Update `.env`

Set the frontend to call the public backend URL:

```bash
NEXT_PUBLIC_API_URL=https://<backend-tunnel>
```

Allow the public frontend URL through backend CORS:

```bash
CORS_ORIGINS=http://localhost:3000,https://<frontend-tunnel>
```

Keep `http://localhost:3000` in `CORS_ORIGINS` if you still want to use the local browser URL.

## 4. Restart Services

The API and web containers read these variables at startup:

```bash
docker compose up -d --force-recreate api web
```

Open the public frontend URL after the restart.

## Optional: Remote PPT/PPTX Uploads

Viewing the app and calling the backend only needs the frontend and backend tunnels.

PPT/PPTX upload uses a presigned MinIO URL. If someone outside your machine needs to upload files from their browser, expose MinIO too:

```bash
ngrok http 9000
```

or:

```bash
cloudflared tunnel --url http://localhost:9000
```

Then keep browser URLs separate from any optional legacy provider URLs. `MINIO_PUBLIC_ENDPOINT` can stay local for a browser running on the same machine. The default Wavespeed talking-photo flow uploads the avatar image to Wavespeed directly, so it does not require a public provider asset URL:

```bash
MINIO_INTERNAL_ENDPOINT=http://minio:9000
MINIO_PUBLIC_ENDPOINT=http://localhost:9000
EXTERNAL_PROVIDER_ASSET_BASE_URL=https://<minio-tunnel-host>
```

Restart the API and worker so new presigned upload URLs use that host:

```bash
docker compose up -d --force-recreate api worker
```

## Common Issues

- CORS error in the browser: add the public frontend URL to `CORS_ORIGINS`, then restart `api`.
- Frontend still calls `localhost:8000`: update `NEXT_PUBLIC_API_URL`, then restart `web`.
- Upload initializes but file upload fails from another computer: expose MinIO and update `MINIO_PUBLIC_ENDPOINT`.
- Legacy WaveSpeed lip-sync returns `EXTERNAL_ASSET_URL_NOT_PUBLIC`: expose MinIO or use external storage, then set `EXTERNAL_PROVIDER_ASSET_BASE_URL` to that public URL. The default talking-photo path does not use this setting.
- ngrok asks for authentication: run `ngrok config add-authtoken <token>` from your ngrok account.
- Tunnel URL changed: update `.env` and restart the affected containers again.

Stop the tunnels with `Ctrl-C` when the demo is finished.
