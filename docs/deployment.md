# Deployment: local server + Cloudflare Tunnel

The site runs on the host machine and is published through a Cloudflare
Tunnel, so no ports are opened on the router.

## 1. Run the site

```bash
python run.py serve
```

It listens on `HOST:PORT` from `.env` (default `127.0.0.1:8311`). Keep it on
`127.0.0.1`: only `cloudflared` on the same machine needs to reach it.

To keep it running after logout on Windows, register it as a scheduled task
or service (for example with NSSM: `nssm install gilead311 <repo>\.venv\Scripts\python.exe run.py serve`,
working directory = repo root).

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
- The server trusts `X-Forwarded-*` headers from `127.0.0.1` only (set in
  `run.py`), which is where `cloudflared` connects from.
