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

## 2. Cloudflare Tunnel

**Live at https://311.scurrycat.ca** (set up 2026-11-02).

The team server already runs a remotely-managed tunnel, `home-docker`
(`cloudflared` installed natively on Windows), which publishes several
`*.scurrycat.ca` apps. The site is one more route on that tunnel:

| Setting | Value |
|---|---|
| Tunnel | `home-docker` (`be337d33-201f-472d-a06d-5b24f08be67d`), managed in the dashboard |
| Published route | `311.scurrycat.ca` -> `http://localhost:8311` |
| DNS | proxied CNAME `311.scurrycat.ca` -> `be337d33-201f-472d-a06d-5b24f08be67d.cfargotunnel.com` |

Because `cloudflared` runs on the host, it reaches the container through the
port published on `127.0.0.1:8311`; nothing in `docker-compose.yml` is
tunnel-specific. To change the route: Cloudflare dashboard > Networking >
Tunnels > `home-docker` > Published application routes. Edit only the
`311.scurrycat.ca` row; the other routes belong to other apps.

Setting this up on a different machine: create a tunnel in the dashboard
(Networking > Tunnels > Create Tunnel), run the connector command it shows,
and add a published application route for your hostname pointing to
`http://localhost:8311`.

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
  Cloudflare's proxy timeout is 100 s; pick faster models (the default
  `qwen3:4b-instruct` replies in about 5-15 s on the team server) if
  requests start timing out.
- Outside Docker the server trusts `X-Forwarded-*` headers from `127.0.0.1`
  only (`FORWARDED_ALLOW_IPS`); in the container it trusts them from the
  Docker network, since the port is only published to the host.
