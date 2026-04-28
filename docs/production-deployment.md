# Production Deployment

This deployment path builds Docker images in GitHub Actions, pushes them to private Docker Hub repositories, and deploys them to a Linux server over SSH.

The setup is intentionally simple. It does not add authentication, TLS termination, managed databases, or production object storage.

## Docker Hub Repositories

Create two private repositories in Docker Hub:

- `your-dockerhub-user/educachile-web`
- `your-dockerhub-user/educachile-api`

The Celery worker uses the same backend image as the API.

Each deployment pushes two tags for both images:

- `latest`
- the full git commit SHA

## GitHub Secrets

Configure these repository secrets:

- `DOCKERHUB_USERNAME`: Docker Hub username.
- `DOCKERHUB_TOKEN`: Docker Hub access token with read/write access to the private repositories.
- `DOCKERHUB_WEB_IMAGE`: Full web image name, for example `your-dockerhub-user/educachile-web`.
- `DOCKERHUB_API_IMAGE`: Full API image name, for example `your-dockerhub-user/educachile-api`.
- `PRODUCTION_HOST`: Server IP address or hostname.
- `PRODUCTION_USER`: SSH username on the server.
- `PRODUCTION_SSH_KEY`: Private SSH key used by GitHub Actions.
- `PRODUCTION_DEPLOY_PATH`: Server directory, for example `/opt/educachile`.

Configure this repository variable:

- `NEXT_PUBLIC_API_URL`: Public backend URL used when building the frontend image, for example `https://api.example.com` or `http://SERVER_IP:8000`.

Do not store the server password in GitHub Actions. Use SSH key authentication.

## Server Preparation

Install Docker and the Compose plugin on the server. For Ubuntu, the high-level steps are:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

Then install Docker using Docker's current Ubuntu instructions for your distribution.

Create the deployment directory:

```bash
sudo mkdir -p /opt/educachile
sudo chown "$USER":"$USER" /opt/educachile
```

Allow only the public ports you need:

- `3000` for the web app, unless you put a reverse proxy in front.
- `8000` for the API, unless routed through a reverse proxy.
- `9000` for MinIO only while the current browser upload flow needs direct object storage access.

Do not expose Postgres or Redis publicly.

## SSH Key Setup

Create a deployment key on your local machine:

```bash
ssh-keygen -t ed25519 -C "github-actions-educachile-production" -f ./educachile_deploy_key
```

Copy the public key to the server:

```bash
ssh-copy-id -i ./educachile_deploy_key.pub user@SERVER_IP
```

Add the private key content to the GitHub secret `PRODUCTION_SSH_KEY`:

```bash
cat ./educachile_deploy_key
```

Test SSH access:

```bash
ssh -i ./educachile_deploy_key user@SERVER_IP
```

## Production Environment File

On the server, create `/opt/educachile/.env.production` from `.env.production.example`.

```bash
cd /opt/educachile
nano .env.production
```

Required values include:

```bash
DOCKERHUB_WEB_IMAGE=your-dockerhub-user/educachile-web
DOCKERHUB_API_IMAGE=your-dockerhub-user/educachile-api
IMAGE_TAG=latest

ENVIRONMENT=production
APP_ENV=production
DEBUG=false
SECRET_KEY=replace-with-a-long-random-secret
ENCRYPTION_KEY=replace-with-a-long-random-encryption-key
ENABLE_DEV_SEED=false
CORS_ORIGINS=https://your-frontend-domain.example

DATABASE_URL=postgresql+psycopg://educachile:replace-with-db-password@postgres:5432/educa_chile
REDIS_URL=redis://redis:6379/0

POSTGRES_DB=educa_chile
POSTGRES_USER=educachile
POSTGRES_PASSWORD=replace-with-db-password

MINIO_ROOT_USER=replace-with-minio-root-user
MINIO_ROOT_PASSWORD=replace-with-minio-root-password
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=replace-with-minio-root-user
MINIO_SECRET_KEY=replace-with-minio-root-password
MINIO_BUCKET=educachile
MINIO_SECURE=false
MINIO_PUBLIC_ENDPOINT=your-server-host-or-domain:9000

NEXT_PUBLIC_API_URL=https://your-api-domain.example
```

Use strong unique values. Do not commit `.env.production`.

## Deployment Flow

On every push to `main`, `.github/workflows/deploy-production.yml`:

1. Checks out the repository.
2. Logs in to Docker Hub.
3. Builds and pushes the web image with `latest` and the commit SHA tags.
4. Builds and pushes the API image with `latest` and the commit SHA tags.
5. Copies `docker-compose.prod.yml` and `scripts/deploy-production.sh` to the server.
6. Connects over SSH.
7. Pulls the selected image tag.
8. Starts Postgres, Redis, MinIO, and API.
9. Runs Alembic migrations inside the API container.
10. Starts web and worker.
11. Prints `docker compose ps`.

The deployment script is safe to rerun:

```bash
cd /opt/educachile
IMAGE_TAG=latest scripts/deploy-production.sh
```

## Manual Rollback

Deploy a previous commit SHA tag:

```bash
cd /opt/educachile
IMAGE_TAG=<previous-commit-sha> scripts/deploy-production.sh
```

If you want that rollback to persist as the default server tag, update `IMAGE_TAG` in `.env.production`.

Database migrations are not automatically rolled back. Review migration history before rolling back across schema changes.

## Logs and Status

Inspect services:

```bash
cd /opt/educachile
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

Follow logs:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f api
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f worker
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f web
```

Run migrations manually:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec api alembic upgrade head
```

## Local Verification Before Pushing

From the repository root:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production.example config
docker build -t educachile-api:test apps/api
docker build --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 -t educachile-web:test apps/web
docker compose exec api pytest
docker compose exec web npm run build
```

The production compose file uses Docker Hub image names from the env file, so it is expected to pull remote images when running for real.
