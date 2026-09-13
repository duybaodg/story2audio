# Deploy Ebook2Audio to Ubuntu with GitHub Actions

This guide deploys the current Docker Compose stack directly to an Ubuntu server using an existing deployment user. Replace every uppercase placeholder, such as `DEPLOY_USER`, before running a command.

## 1. Confirm the server and deployment user

Supported baseline:

- Ubuntu 22.04 LTS or newer.
- An existing non-root deployment user.
- `sudo` access for installing Docker.
- SSH key access to the server.
- Enough disk and memory for the VieNeu model and generated files.

From your computer:

```bash
ssh -p SSH_PORT DEPLOY_USER@SERVER_IP
```

On the server, confirm the account:

```bash
id
uname -m
lsb_release -ds
```

## 2. Install Git and Docker

Skip this step if these commands already work:

```bash
git --version
docker --version
docker compose version
```

Otherwise, install Docker from its official Ubuntu repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

These commands follow Docker's [official Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/).

## 3. Allow the deployment user to run Docker

The GitHub Actions workflow runs `docker` without `sudo`, so the deployment user needs Docker access:

```bash
sudo usermod -aG docker "$(id -un)"
exit
```

Reconnect so the new group membership takes effect:

```bash
ssh -p SSH_PORT DEPLOY_USER@SERVER_IP
docker run --rm hello-world
docker compose version
```

Membership in the `docker` group grants root-level control of the server. Keep this deployment account dedicated and protect its SSH key. See Docker's [post-installation security warning](https://docs.docker.com/engine/install/linux-postinstall/).

## 4. Clone the repository

Use a directory owned by the deployment user:

```bash
APP_DIR="/home/DEPLOY_USER/story2audio"
git clone https://github.com/duybaodg/story2audio.git "$APP_DIR"
cd "$APP_DIR"
```

Use the same absolute path later for the GitHub `VPS_PATH` variable.

## 5. Create the server environment file

```bash
cd "/home/DEPLOY_USER/story2audio"
cp -n .env.example .env
chmod 600 .env
nano .env
```

Generate a server-only signing secret and add it to `.env`:

```bash
printf 'SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" >> .env
```

Do not store `SESSION_SECRET` in GitHub; it belongs only in the server `.env`.

For direct access on port 8000, keep:

```env
APP_PORT=8000
TRUST_PROXY_HEADERS=false
SESSION_COOKIE_SECURE=false
```

Optionally add `HF_TOKEN` to reduce anonymous Hugging Face download limitations. Never commit `.env`.

For the public Nginx/HTTPS deployment, use:

```env
APP_PORT=127.0.0.1:8000
TRUST_PROXY_HEADERS=true
SESSION_COOKIE_SECURE=true
ENABLE_GLOBAL_CACHE_CLEAR=false
```

Nginx must overwrite `X-Forwarded-For` with the connecting client address. The
application will refuse to start with secure cookies if `SESSION_SECRET` is
missing, too short, or still contains the example value.

## 6. Choose how the application is reachable

### Private access through an SSH tunnel

This is the safer option when only you need access. Set:

```env
APP_PORT=127.0.0.1:8000
```

After starting the stack, open a tunnel from your computer:

```bash
ssh -p SSH_PORT -L 8000:127.0.0.1:8000 DEPLOY_USER@SERVER_IP
```

Then open `http://127.0.0.1:8000` locally.

### Direct public HTTP access

Keep `APP_PORT=8000`, allow TCP port 8000 in the hosting provider's network firewall, and open:

```text
http://SERVER_IP:8000
```

This connection is unencrypted. Do not upload sensitive documents over public HTTP.

Docker-published ports can bypass ordinary UFW rules. Use the hosting provider firewall or correctly configured Docker firewall rules when restricting port 8000. See Docker's [firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/).

Always ensure the SSH port is allowed before changing or enabling a firewall, or you can lock yourself out of the server.

## 7. Validate and start the stack once

On the server:

```bash
cd "/home/DEPLOY_USER/story2audio"
docker compose --profile vieneu config --quiet
docker compose --profile vieneu up -d --build --wait
```

The first VieNeu start downloads and warms the model, so it can take several minutes. Watch progress in another SSH session:

```bash
cd "/home/DEPLOY_USER/story2audio"
docker compose --profile vieneu logs -f vieneu-worker
```

Verify all services:

```bash
docker compose --profile vieneu ps
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/tts/health
```

Expected health responses:

- `/health`: HTTP 200 with `"ok": true`.
- `/tts/health`: HTTP 200 with `"vieneu_ready": true`.

## 8. Create a dedicated GitHub Actions SSH key

Run these commands on your computer, not on the server:

```bash
ssh-keygen -t ed25519 -N '' -C "github-actions-story2audio" -f ~/.ssh/story2audio_actions
ssh-copy-id -i ~/.ssh/story2audio_actions.pub -p SSH_PORT DEPLOY_USER@SERVER_IP
```

This creates a dedicated key without a passphrase because the non-interactive workflow cannot answer a passphrase prompt. Store the private key only in the GitHub environment secret.

Test the exact key:

```bash
ssh -i ~/.ssh/story2audio_actions -p SSH_PORT DEPLOY_USER@SERVER_IP 'docker compose version'
```

Collect the server host key using the same hostname or IP and port that GitHub Actions will use:

```bash
ssh-keyscan -p SSH_PORT -H SERVER_IP > story2audio_known_hosts
ssh-keygen -lf story2audio_known_hosts
```

Verify the displayed fingerprint against the server's SSH host-key fingerprint before trusting it:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Run the `sudo ssh-keygen` command on the server. Run the other commands on your computer.

## 9. Configure the GitHub production environment

In GitHub:

1. Open the repository.
2. Go to **Settings → Environments**.
3. Create or open the environment named `production`.
4. Restrict deployment branches to `main`.
5. Add a required reviewer if manual production approval is wanted.

Add these environment secrets:

| Secret | Value |
| --- | --- |
| `VPS_HOST` | The same `SERVER_IP` or hostname used by `ssh-keyscan`. |
| `VPS_USER` | The existing `DEPLOY_USER`. |
| `VPS_SSH_KEY` | The complete contents of `~/.ssh/story2audio_actions`, including the BEGIN/END lines. |
| `VPS_KNOWN_HOSTS` | The complete contents of `story2audio_known_hosts`. |

Add these environment variables:

| Variable | Value |
| --- | --- |
| `VPS_PATH` | `/home/DEPLOY_USER/story2audio` |
| `VPS_PORT` | The SSH port, normally `22`. |

Environment protection rules and secrets are described in the [GitHub Actions environment documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).

## 10. Trigger deployment

Pull requests run tests and build the Docker image without deploying. Merging or pushing to `main` runs the same checks and then deploys the exact tested commit:

```text
push to main
  → pytest and syntax checks
  → Docker Compose validation and image build
  → SSH to the Ubuntu server
  → checkout the tested commit
  → docker compose up --build --wait
```

Watch the run under **GitHub → Actions → CI/CD**.

## 11. Verify after every deployment

On the server:

```bash
cd "/home/DEPLOY_USER/story2audio"
docker compose --profile vieneu ps
docker compose --profile vieneu logs --tail=100
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/tts/health
```

Persistent data is stored in Docker volumes. Do not run `docker compose down -v` unless you intentionally want to delete cached audio, documents, jobs, models, and Redis data.

## Troubleshooting

### GitHub deployment exits with code 255

The SSH connection failed before deployment. Check:

- `VPS_HOST`, `VPS_USER`, and `VPS_PORT`.
- The private key matches an entry in the deployment user's `authorized_keys`.
- `VPS_KNOWN_HOSTS` was generated for the same hostname and port.
- The hosting provider firewall permits the SSH port.

### Docker permission denied

Confirm the deployment user is in the Docker group, then disconnect and reconnect:

```bash
id -nG
docker ps
```

### Deployment reports a missing environment file

```bash
cd "/home/DEPLOY_USER/story2audio"
cp -n .env.example .env
chmod 600 .env
```

Review `.env` before rerunning the workflow.

### VieNeu worker is unhealthy

```bash
cd "/home/DEPLOY_USER/story2audio"
docker compose --profile vieneu logs --tail=200 vieneu-worker
docker compose --profile vieneu restart vieneu-worker
```

Check available memory, disk space, outbound network access, and `HF_TOKEN`.

## Roll back

Find a previously successful commit SHA in GitHub Actions, then run on the server:

```bash
cd "/home/DEPLOY_USER/story2audio"
git fetch --depth=1 origin GOOD_COMMIT_SHA
git checkout --detach GOOD_COMMIT_SHA
docker compose --profile vieneu up -d --build --remove-orphans --wait
```

Verify both health endpoints after rollback.
