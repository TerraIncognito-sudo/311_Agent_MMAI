# Deployment: local server + Cloudflare Tunnel

The site runs on the host machine and is published through a Cloudflare
Tunnel, so no ports are opened on the router.

## 1. Run the site (Docker)

The server runs the site as a Docker container (`docker-compose.yml`). On the
team server (192.168.1.149) the repo is cloned at `C:\Users\nick\311_Agent_MMAI`.

First time:

```bash
git clone https://github.com/TerraIncognito-sudo/311_Agent_MMAI.git
cd 311_Agent_MMAI
copy .env.example .env
docker compose up -d --build
```

(`cp` instead of `copy` on macOS/Linux; fill in the Supabase values in `.env`.)

Update to the latest `main`:

```bash
git pull
docker compose up -d --build
```

`config/` and `data/kb/` are mounted into the container, so for policy,
prompt or knowledge-base changes alone, `git pull` then
`docker compose restart` is enough.

| Task | Command |
|---|---|
| Logs | `docker compose logs -f` |
| Health | `curl http://127.0.0.1:8311/api/health` |
| Load / refresh the knowledge base | `docker compose run --rm app python run.py ingest` |
| Stop | `docker compose down` |

Notes:
- The port is published on `127.0.0.1:8311` (this machine only, for
  `cloudflared`). Set `PUBLISH_ADDRESS=0.0.0.0` in `.env` to also reach it
  from the LAN at `http://<server-ip>:8311`.
- Ollama runs on the host, not in the container; the container reaches it at
  `http://host.docker.internal:11434` (`OLLAMA_BASE_URL_DOCKER` in `.env`).
  On the team server Ollama runs in the `yick1` desktop session, so local
  models are available only while that user is logged in.
- `restart: unless-stopped` brings the container back after a reboot once
  Docker is running.
- Without Docker: `python run.py serve` (listens on `HOST:PORT` from `.env`,
  default `127.0.0.1:8311`).

## 2. Create the tunnel

Install `cloudflared`, then (once):

```bash
cloudflared tunnel login
cloudflared tunnel create gilead-311
cloudflared tunnel route dns gilead-311 311.<your-domain>
```

Config file (`%USERPROFILE%\.cloudflared\config.yml` on Windows,
`~/.cloudflared/config.yml` elsewhere). Never commit the credentials JSON:

```yaml
tunnel: gilead-311
credentials-file: C:\Users\<you>\.cloudflared\<tunnel-id>.json
ingress:
  - hostname: 311.<your-domain>
    service: http://127.0.0.1:8311
  - service: http_status:404
```

Run it, or install it as a service so it starts with the machine:

```bash
cloudflared tunnel run gilead-311
```

```bash
cloudflared service install
```

Alternative: create the tunnel in the Cloudflare dashboard (Zero Trust >
Networks > Tunnels), add a public hostname pointing to
`http://127.0.0.1:8311`, and run the connector command it gives you.

## 3. Things to know before sharing the link

- **Ollama is your hardware.** Anyone with the link who picks "Local
  (Ollama)" runs models on this machine. To limit who can use the site, put
  it behind Cloudflare Access (Zero Trust > Access > Applications) with an
  email allow-list for the team and graders.
- **OpenRouter keys** go browser -> Cloudflare (HTTPS) -> this server ->
  OpenRouter on each request. The server holds the key only for the duration
  of that request; it is not written to disk, the database, logs or the
  conversation state. Uvicorn access logs record paths, never headers.
- **Long replies.** Local thinking models can take 30-90 s per turn.
  Cloudflare's proxy timeout is 100 s; pick faster models (for example
  `qwen2.5:7b`) if requests start timing out.
- Outside Docker the server trusts `X-Forwarded-*` headers from `127.0.0.1`
  only (`FORWARDED_ALLOW_IPS`); in the container it trusts them from the
  Docker network, since the port is only published to the host.
