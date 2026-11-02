# Deployment: local server + Cloudflare Tunnel

The site runs on the host machine and is published through a Cloudflare
Tunnel, so no ports are opened on the router.

## 1. Run the site

```bash
python run.py serve
```

It listens on `HOST:PORT` from `.env` (default `127.0.0.1:8311`). Keep it on
`127.0.0.1`: only `cloudflared` on the same machine needs to reach it.

### Team server (Windows, 192.168.1.149)

The site runs natively (no Docker) from `C:\Users\nick\311_Agent_MMAI`, a
clone of this repo, with its own `.venv` and `.env`. A scheduled task named
`Gilead311` runs `deploy\windows\run_server.cmd` as `nick` at system startup
(S4U: runs without anyone logged in, no stored password), with no time
limit and a restart every minute if it exits. Output goes to
`logs\server.log`. Ollama on the same machine is reached at
`http://localhost:11434`; it runs in the `yick1` desktop session, so local
models are available only while that user is logged in.

- Update to the latest `main`: `deploy\windows\update.cmd` (pull, install, restart).
- Restart only: `schtasks /end /tn Gilead311` then `schtasks /run /tn Gilead311`.

One-time setup (already done on the team server):

```bash
git clone https://github.com/TerraIncognito-sudo/311_Agent_MMAI.git
cd 311_Agent_MMAI
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Then register the task (PowerShell, as an administrator):

```powershell
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c "C:\Users\nick\311_Agent_MMAI\deploy\windows\run_server.cmd"' -WorkingDirectory 'C:\Users\nick\311_Agent_MMAI'
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName Gilead311 -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal (New-ScheduledTaskPrincipal -UserId nick -LogonType S4U) -Settings $settings
Start-ScheduledTask -TaskName Gilead311
```

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
