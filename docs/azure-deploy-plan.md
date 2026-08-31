# Azure Deployment Plan

This is a step-by-step deployment runbook for Ebook2Audio on Azure using:

- Azure Container Registry for Docker images
- Azure Container Apps for the web app and VieNeu worker
- Managed Redis for queueing and rate limiting
- Azure Files for shared persistent storage
- Log Analytics for logs

For architecture, sizing, and versioning details, see [`azure-deployment.md`](azure-deployment.md).

## 0. Target Architecture

Deploy these runtime components:

```text
Internet
  -> ebook2audio-app
      FastAPI + UI + upload/status/download endpoints
      no local VieNeu model initialization

ebook2audio-app
  -> Redis
      upload rate limits
      VieNeu queue
      VieNeu cancellation flags

ebook2audio-vieneu-worker
  -> Redis
      pulls one VieNeu job at a time
  -> Azure Files
      writes audio + status metadata

Azure Files
  -> /app/audio_cache
  -> /app/documents
  -> /app/jobs
  -> /root/.cache/huggingface
```

## 1. Pre-Deployment Checklist

Confirm local code is deployable:

```bash
uv run python -m py_compile main.py tts_queue.py tts_worker.py
uv run pytest
docker compose --profile vieneu config --quiet
```

Choose release version:

```text
APP_VERSION=v4.0.0
IMAGE_TAG=v4.0.0
```

Update `pyproject.toml` if this is a new release:

```toml
version = "4.0.0"
```

Create a Git tag after tests pass:

```bash
git tag v4.0.0
git push origin v4.0.0
```

## 2. Decide Azure Names

Use consistent names before creating resources:

```bash
LOCATION=eastus
RESOURCE_GROUP=rg-ebook2audio-prod
ACR_NAME=ebook2audioregistry
CONTAINER_ENV=cae-ebook2audio-prod
APP_NAME=ebook2audio-app
WORKER_NAME=ebook2audio-vieneu-worker
STORAGE_ACCOUNT=ebook2audiostorage
FILE_SHARE=ebook2audio-share
LOG_WORKSPACE=law-ebook2audio-prod
REDIS_NAME=redis-ebook2audio-prod
IMAGE_TAG=v4.0.0
```

Pick a region where your required Container Apps CPU/memory and managed Redis options are available.

## 3. Create Core Azure Resources

Create a resource group:

```bash
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION"
```

Create Log Analytics:

```bash
az monitor log-analytics workspace create \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "$LOG_WORKSPACE" \
  --location "$LOCATION"
```

Create Azure Container Registry:

```bash
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled false
```

Create the Container Apps environment:

```bash
az containerapp env create \
  --name "$CONTAINER_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --logs-workspace-id "$(az monitor log-analytics workspace show \
      --resource-group "$RESOURCE_GROUP" \
      --workspace-name "$LOG_WORKSPACE" \
      --query customerId -o tsv)" \
  --logs-workspace-key "$(az monitor log-analytics workspace get-shared-keys \
      --resource-group "$RESOURCE_GROUP" \
      --workspace-name "$LOG_WORKSPACE" \
      --query primarySharedKey -o tsv)"
```

## 4. Build and Push the Docker Image

Login to Azure Container Registry:

```bash
az acr login --name "$ACR_NAME"
```

Build and push:

```bash
ACR_LOGIN_SERVER="$(az acr show \
  --name "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query loginServer -o tsv)"

docker build -t "$ACR_LOGIN_SERVER/ebook2audio:$IMAGE_TAG" .
docker push "$ACR_LOGIN_SERVER/ebook2audio:$IMAGE_TAG"
```

Optional `latest` tag:

```bash
docker tag "$ACR_LOGIN_SERVER/ebook2audio:$IMAGE_TAG" "$ACR_LOGIN_SERVER/ebook2audio:latest"
docker push "$ACR_LOGIN_SERVER/ebook2audio:latest"
```

Use immutable version tags for production rollbacks. Avoid deploying only `latest`.

## 5. Create Persistent Azure Files Storage

Create a storage account:

```bash
az storage account create \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku Standard_LRS
```

Get the storage key:

```bash
STORAGE_KEY="$(az storage account keys list \
  --resource-group "$RESOURCE_GROUP" \
  --account-name "$STORAGE_ACCOUNT" \
  --query '[0].value' -o tsv)"
```

Create one file share:

```bash
az storage share-rm create \
  --resource-group "$RESOURCE_GROUP" \
  --storage-account "$STORAGE_ACCOUNT" \
  --name "$FILE_SHARE" \
  --quota 100
```

Register the file share with the Container Apps environment:

```bash
az containerapp env storage set \
  --name "$CONTAINER_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --storage-name ebook2audiofiles \
  --storage-type AzureFile \
  --azure-file-account-name "$STORAGE_ACCOUNT" \
  --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name "$FILE_SHARE" \
  --access-mode ReadWrite
```

This share will hold audio, documents, jobs, and Hugging Face model cache.

## 6. Create Managed Redis

Use Azure Managed Redis if available in your region. If not, create Azure Cache for Redis.

The app needs a Redis connection string:

```text
REDIS_URL=rediss://:<password>@<host>:6380/0
```

For local Docker Compose the URL is:

```text
redis://redis:6379
```

For Azure, prefer TLS (`rediss://`) when using a managed Redis endpoint.

Store the final value as a Container Apps secret in later steps.

## 7. Create the Web App Container App

Create the web app with external ingress:

```bash
az containerapp create \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_ENV" \
  --image "$ACR_LOGIN_SERVER/ebook2audio:$IMAGE_TAG" \
  --target-port 8000 \
  --ingress external \
  --registry-server "$ACR_LOGIN_SERVER" \
  --cpu 1.0 \
  --memory 2Gi \
  --min-replicas 1 \
  --max-replicas 2
```

Assign the Container App identity permission to pull from ACR if you use managed identity. If you use registry credentials instead, configure them as secrets.

Set app secrets:

```bash
az containerapp secret set \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --secrets \
    redis-url="<REDIS_URL>" \
    hf-token="<HF_TOKEN_OR_EMPTY>"
```

Set app environment variables:

```bash
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --set-env-vars \
    APP_VERSION="$IMAGE_TAG" \
    ENABLE_DEBUG_TTS=false \
    DOCUMENT_STORAGE_PATH=/app/documents \
    JOBS_DIR=/app/jobs \
    MAX_WORKERS=1 \
    VIENEU_INIT_IN_WEB=false \
    VIENEU_MAX_WORKERS=1 \
    VIENEU_SAMPLE_RATE=48000 \
    VIENEU_CHUNK_SIZE=500 \
    VIENEU_WARMUP_ITERATIONS=1 \
    TTS_STREAM_FIRST_BYTE_TIMEOUT_SECONDS=300 \
    REDIS_URL=secretref:redis-url \
    HF_TOKEN=secretref:hf-token
```

Mount Azure Files paths. Container Apps volume mounts are easiest to maintain through YAML. Export the app:

```bash
az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  -o yaml > app.yaml
```

In `app.yaml`, add an Azure Files volume under `template.volumes`:

```yaml
volumes:
  - name: ebook2audiofiles
    storageType: AzureFile
    storageName: ebook2audiofiles
```

Add volume mounts to the app container:

```yaml
volumeMounts:
  - volumeName: ebook2audiofiles
    mountPath: /app/audio_cache
    subPath: audio_cache
  - volumeName: ebook2audiofiles
    mountPath: /app/documents
    subPath: documents
  - volumeName: ebook2audiofiles
    mountPath: /app/jobs
    subPath: jobs
  - volumeName: ebook2audiofiles
    mountPath: /root/.cache/huggingface
    subPath: huggingface
```

Apply the YAML:

```bash
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --yaml app.yaml
```

## 8. Create the VieNeu Worker Container App

Create an internal/no-ingress worker:

```bash
az containerapp create \
  --name "$WORKER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_ENV" \
  --image "$ACR_LOGIN_SERVER/ebook2audio:$IMAGE_TAG" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --command "/app/.venv/bin/python" \
  --args "tts_worker.py" \
  --cpu 2.0 \
  --memory 8Gi \
  --min-replicas 1 \
  --max-replicas 1
```

Set worker secrets:

```bash
az containerapp secret set \
  --name "$WORKER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --secrets \
    redis-url="<REDIS_URL>" \
    hf-token="<HF_TOKEN_OR_EMPTY>"
```

Set worker environment variables:

```bash
az containerapp update \
  --name "$WORKER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --set-env-vars \
    APP_VERSION="$IMAGE_TAG" \
    DOCUMENT_STORAGE_PATH=/app/documents \
    JOBS_DIR=/app/jobs \
    MAX_WORKERS=1 \
    VIENEU_MAX_WORKERS=1 \
    VIENEU_SAMPLE_RATE=48000 \
    VIENEU_CHUNK_SIZE=500 \
    VIENEU_WARMUP_ITERATIONS=1 \
    REDIS_URL=secretref:redis-url \
    HF_TOKEN=secretref:hf-token
```

Mount the same Azure Files paths by exporting and editing worker YAML:

```bash
az containerapp show \
  --name "$WORKER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  -o yaml > worker.yaml
```

Add the same `template.volumes` and `volumeMounts` entries used for the web app, then apply:

```bash
az containerapp update \
  --name "$WORKER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --yaml worker.yaml
```

## 9. Validate Deployment

Get the app URL:

```bash
APP_URL="https://$(az containerapp show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn -o tsv)"

echo "$APP_URL"
```

Check health:

```bash
curl "$APP_URL/health"
curl "$APP_URL/tts/health"
```

Expected web app logs:

```text
VieNeu model initialization skipped in web process
Rate limiter initialized
```

Expected worker logs:

```text
Initializing VieNeu model in worker process
VieNeu worker ready
```

Watch logs:

```bash
az containerapp logs show \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --follow

az containerapp logs show \
  --name "$WORKER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --follow
```

## 10. End-to-End Smoke Test

Open the app URL in a browser.

Test Edge/gTTS first:

```text
Engine: Edge-TTS
Language: Vietnamese
Text: Xin chào, đây là kiểm tra triển khai.
Expected: audio starts or completes normally
```

Test VieNeu with a very short text:

```text
Engine: VieNeu-TTS
Language: Vietnamese only
Text: Xin chào.
Expected:
- /tts/start returns a cache_id
- status moves queued -> processing/generating -> completed
- worker logs show job started and finished
- audio can play/download
```

Do not start with a long book-length request. First validate the smallest possible VieNeu job.

## 11. Monitoring Checklist

Monitor these signals after the first deploy:

```text
app CPU and memory
worker CPU and memory
worker restarts
Redis memory
Redis connection count
Azure Files capacity
Azure Files latency
HTTP 5xx rate
TTS job failure rate
average VieNeu job duration
```

Useful log strings:

```text
Unable to enqueue VieNeu TTS job
VieNeu job failed
Stream first-byte timeout
Failed to initialize VieNeu model pool
```

## 12. Rollback Plan

Keep the previous image tag available, for example:

```text
ebook2audio:v4.0.0
ebook2audio:v4.0.1
```

Rollback the web app:

```bash
az containerapp update \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$ACR_LOGIN_SERVER/ebook2audio:v4.0.0"
```

Rollback the worker:

```bash
az containerapp update \
  --name "$WORKER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$ACR_LOGIN_SERVER/ebook2audio:v4.0.0"
```

If Redis queue data is incompatible between versions, drain or clear only the VieNeu queue keys after confirming no active jobs should continue:

```text
ebook2audio:tts:vieneu:queue
ebook2audio:tts:vieneu:processing
ebook2audio:tts:cancel:<cache_id>
```

Do not delete Azure Files during rollback. It contains generated audio, user documents, job files, and model cache.

## 13. Cost Control

Start small:

```text
app: 1 vCPU / 2 GiB
worker: 2 vCPU / 6-8 GiB
worker replicas: 1
Redis: small managed tier
Azure Files: 100 GiB quota
```

Reduce costs by:

- keeping `ebook2audio-vieneu-worker` at one replica
- keeping `VIENEU_MAX_WORKERS=1`
- caching Hugging Face models on Azure Files
- using short test requests during validation
- avoiding GPU until CPU deployment is stable

## 14. Final Acceptance Criteria

Deployment is ready when:

- [ ] `/health` returns the expected `APP_VERSION`.
- [ ] Web app logs say VieNeu initialization is skipped.
- [ ] Worker logs say `VieNeu worker ready`.
- [ ] Edge/gTTS request succeeds.
- [ ] VieNeu short request succeeds.
- [ ] Generated audio persists after app restart.
- [ ] Generated audio persists after worker restart.
- [ ] Redis queue does not accumulate stuck jobs.
- [ ] Azure Files usage and worker memory are within expected limits.
