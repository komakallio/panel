# Deploying the KK Dashboard

Manual deployment on a fresh Ubuntu server (Hetzner Cloud or similar). The app
runs in Docker behind **Caddy**, which serves it over HTTPS with an automatic
Let's Encrypt certificate. Everything except per-server secrets lives in this repo.

## What you need
- **A server:** small is plenty — Hetzner **CX23** or **CAX11** (2 vCPU / 4 GB / 40 GB),
  Ubuntu LTS, **with IPv4** (some sources are IPv4-only, and IPv4-only client
  networks need it).
- **A hostname** resolving to the server's IP. HTTPS is required — the browser
  geolocation feature only works in a secure context. A free `*.dy.fi` name works;
  a real subdomain (e.g. `panel.komakallio.fi`) is nicer.
- **SSH access** as `root` with your key (Hetzner attaches it at creation).

Replace `<ip>`, `YOURNAME.dy.fi`, and the dy.fi credentials below with your own.
(`deploy` here is just an example admin username — use whatever you like.)

## 1. Harden the host — first login, as `root`
```bash
apt update && apt -y upgrade

# non-root sudo user, reusing root's SSH key
adduser --gecos "" deploy                     # prompts for a password (used by sudo)
usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
```
**In a new terminal, verify `ssh deploy@<ip>` works and `sudo whoami` prints `root`
— before locking root out.** Then, back as root:
```bash
printf 'PermitRootLogin no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n' \
  > /etc/ssh/sshd_config.d/99-hardening.conf
systemctl restart ssh

ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

apt -y install unattended-upgrades && systemctl enable --now unattended-upgrades
timedatectl set-timezone Europe/Helsinki
```
From here on you log in as `deploy`.

## 2. Install Docker (as `deploy`)
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker                # or log out/in, to use docker without sudo
docker compose version       # sanity check
docker run --rm hello-world  # should print "Hello from Docker!"
```

## 3. Point a hostname at the server
HTTPS needs a name resolving to the box. Using **dy.fi** (it sets the host to the
request's *source IP*, so run the update **from the server**):
```bash
# register YOURNAME.dy.fi on dy.fi first, then:
curl -A "kk-panel/1.0" -u 'DYFI_EMAIL:DYFI_PASSWORD' \
  'https://www.dy.fi/nic/update?hostname=YOURNAME.dy.fi'
getent hosts YOURNAME.dy.fi   # should show the server IP
```
For a real domain instead, ask the DNS admin for an `A` record: `name → server IP`.

## 4. Get the code + configure
```bash
cd ~
git clone https://github.com/komakallio/panel.git
cd panel
cp .env.example .env
nano .env                     # set SITE_HOST=YOURNAME.dy.fi
```

## 5. Launch
```bash
docker compose up -d --build
```
Caddy fetches the TLS cert on the first request (a few seconds). Open
`https://YOURNAME.dy.fi` and check:
- the dashboard loads and tiles populate,
- the location dropdown's **Geolocation** option works (secure context),
- the Komakallio allsky updates in near real time (WebSocket push).

## 6. Keep the dy.fi name alive (cron)
dy.fi names expire after ~7 days without an update. As `deploy`, `crontab -e`:
```
17 4 * * *  curl -fsS -A "kk-panel/1.0" -u 'DYFI_EMAIL:DYFI_PASSWORD' 'https://www.dy.fi/nic/update?hostname=YOURNAME.dy.fi' >/dev/null
```
(Drop this once you move to a real domain with a static A record.)

## Deploying updates
After changes are merged to `main` on GitHub:
```bash
ssh deploy@<ip>               # or your SSH config alias
cd ~/panel && ./deploy.sh
```
`deploy.sh` = `git pull` + `docker compose up -d --build` + image prune + log tail.
Caddy stays up; only the app rebuilds.

- **Config-only change** (`config/*.yaml` — bind-mounted): `git pull && docker compose restart app` (no rebuild needed).
- **Rollback:** `git checkout <good-sha> && docker compose up -d --build`, or revert on GitHub and re-run `./deploy.sh`.

## Adding another hostname (e.g. panel.komakallio.fi)
Once its A record points at the server:
```bash
nano .env                     # SITE_HOST=YOURNAME.dy.fi, panel.komakallio.fi
docker compose up -d          # Caddy fetches the new cert automatically
```

## Reference
- **Secrets** (dy.fi password, `SITE_HOST`) live only in `.env` on the server — gitignored, never committed.
- **Persistence:** archived images live in `./archive` (a bind mount); per-source retention/cleanup is automatic.
- **Ports:** only 22/80/443 are public; the app listens on 8000 inside the Docker network, reached only by Caddy.
- **Logs:** `docker compose logs -f app` (or `caddy`).
- **Stop / restart:** `docker compose down` / `docker compose restart`.
