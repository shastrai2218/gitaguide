# Deploying the API on AWS EC2 (frontend on Vercel)

This replaces **Step 3 (Render)** and **Step 6 (keep-warm)** of
[DEPLOY.md](DEPLOY.md). Everything else in that document is unchanged: AuraDB
and Supabase stay exactly as they are, the graph stays INT8, and the frontend is
still a Vercel project. The API moves from Render to one EC2 instance you own.

```
  browser ──https──▶ Vercel (UI)
     │
     └───https──▶ Caddy :443 ──▶ API :8000 ──▶ AuraDB, Supabase, NVIDIA NIM
                  (one EC2 instance, docker compose)
```

**You do the console work.** Nothing here logs into AWS for you, and no key is
ever committed. Secrets go into `.env.vm` on the instance, which is gitignored.

Set aside about an hour. The first image build is the long part.

---

## What you gain, and what you take on

- **No cold starts.** The instance does not sleep, so the keep-warm workflow and
  the 30-60 s first-message wait go away.
- **You now operate a server.** OS updates, disk and the Docker daemon are yours.
- **It costs money after any free allowance.** Check the current numbers in the
  AWS pricing calculator rather than trusting a figure written here; a t3.small
  is in the low tens of dollars a month. Set a budget alert (step 1) so a
  surprise is an email, not a bill.

---

## Step 1: Account, region, budget alert

1. Sign up at <https://aws.amazon.com/free/>. A card is required.
2. Sign in to the console at <https://console.aws.amazon.com/> and pick the
   **region** in the top-right. Choose one near your testers and near where
   Supabase and Aura live; every turn makes a dozen or more round trips to
   both. `ap-south-1` (Mumbai) is the natural choice for testers in India.

   This matters more than it looks. Measured on 19 Sep 2026 with Supabase in
   Mumbai and Aura in the Singapore area: from an instance in Stockholm each
   query cost 309 ms (Supabase) and 400 ms (Aura), and one real turn took 27.7 s
   with `persist` alone at 3.1 s. From Mumbai the same TCP round trips were
   1 ms and 61 ms (Stockholm: 146 ms and 197 ms), and a turn took 15.5 s with
   `persist` at 90 ms. Find each service's region first (Supabase: Project
   Settings; Aura: instance details), then put the API in the region closest to
   both. Keep the AWS console on that region the whole time: an Elastic IP and
   a key pair belong to one region and are invisible from another.
3. **Billing → Budgets → Create budget** → a monthly cost budget with an email
   alert at a figure you are comfortable with.

## Step 2: Choose the instance type

| Type | Memory | Notes |
| --- | --- | --- |
| **t3.small** | 2 GB | **Recommended.** A turn peaks near 430 MiB, so this has real headroom and the image builds without swap gymnastics. |
| **t3.micro** | 1 GB | Works, tightly, and only with the swap in step 5. Free-tier eligible on accounts that still have the 12-month allowance. |
| t4g.* (Graviton, ARM) | | **Avoid.** The INT8 encoder file is `model_quint8_avx2.onnx`, built for x86 AVX2. It has not been tested on ARM, and if it loads but drifts, retrieval silently degrades. Stay on x86 (`t3`, `t3a`, `m6i`, ...). |

## Step 3: Launch the instance

**EC2 → Instances → Launch instances**:

1. **Name:** `gitaguide-api`.
2. **AMI:** *Ubuntu Server 24.04 LTS*, architecture **64-bit (x86)**.
3. **Instance type:** from step 2.
4. **Key pair → Create new key pair:** RSA, `.pem`. **It downloads once.** Keep it
   somewhere safe; it is the only way in.
5. **Network settings → Edit → Create security group**, with three inbound rules:

   | Type | Port | Source |
   | --- | --- | --- |
   | SSH | 22 | **My IP** (not `0.0.0.0/0`) |
   | HTTP | 80 | Anywhere (`0.0.0.0/0`) |
   | HTTPS | 443 | Anywhere (`0.0.0.0/0`) |

   Do **not** open `8000`; the API is reachable only through Caddy. Port 80 must
   be open even though the site is https: Let's Encrypt validates over it.

   **If SSH times out although the "My IP" rule looks right:** some home and
   mobile ISPs send web traffic and SSH out through *different* public
   addresses, so the console's "My IP" (what a web page sees) is not the address
   your SSH connection arrives from. On 19 Sep 2026 the console said
   `152.59.61.98` while the server saw SSH from `152.59.56.189`. Find the real one
   from an instance you can already reach with `echo ${SSH_CLIENT%% *}`, or
   allow the provider's block (for example `152.59.0.0/16`) instead of one
   address. Ports 80/443 answering "refused" while 22 times out means the
   security group, not the instance, is dropping SSH.
6. **Storage:** raise the root volume from the default 8 GB to **20 GB** (gp3).
   The image, model cache and build layers do not fit comfortably in 8.
7. **Launch instance.**

Unlike some other clouds, Ubuntu on EC2 has no extra firewall inside the
instance. The security group is the only gate.

## Step 4: A fixed IP

An instance's public IP changes when it is stopped and started, and your
hostname is derived from it.

**EC2 → Network & Security → Elastic IPs → Allocate Elastic IP address →
Allocate**, then **Actions → Associate Elastic IP address** → select the
instance. AWS charges for public IPv4 addresses, associated or not; that is part
of the cost in the calculator, and an *unassociated* Elastic IP is billed too, so
release one you stop using.

## Step 5: Connect and prepare

```bash
ssh -i path/to/key.pem ubuntu@<elastic-ip>
```

On Windows, if OpenSSH refuses with *"UNPROTECTED PRIVATE KEY FILE"*, restrict
the key to your user first (PowerShell):

```powershell
icacls key.pem /inheritance:r /grant:r "$($env:USERNAME):R"
```

Then, on the instance:

```bash
# Swap. Required on a 1 GB t3.micro; a cheap safety net on a t3.small.
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Docker (Docker's own convenience script, per docs.docker.com/engine/install/ubuntu)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && newgrp docker

git clone https://github.com/shastrai2218/gitaguide.git && cd gitaguide
git checkout <the branch you deploy from>
```

If the repository is private, `git clone` will ask for credentials: use a GitHub
personal access token with read access, or add a deploy key. Do not put your
account password or a broad token on a shared machine.

## Step 6: A hostname, then the environment file

The API needs an **https** hostname: the Vercel page is https, and a browser
refuses to let it call an http API.

**Quickest, no signup:** [sslip.io](https://sslip.io) resolves any name that
contains an IP. For Elastic IP `13.233.10.20` use `13-233-10-20.sslip.io`. A
DuckDNS name or a domain you own, with an A record to the IP, is more durable.

```bash
cp .env.vm.example .env.vm && chmod 600 .env.vm
nano .env.vm
```

Fill in `API_DOMAIN` and every `REPLACE_ME`, taking the values from the same
places as DEPLOY.md steps 1 and 2: the Supabase DSN with `+asyncpg` and no
`sslmode`, and the Aura URI, username, database and password. On Aura Free the
username and database are the **instance id**, not `neo4j`.

**Supabase: use the Session pooler (port 5432), not the Transaction pooler
(6543) that DEPLOY.md names.** Measured on 19 Sep 2026: 75 concurrent
parameterised queries through this app's SQLAlchemy + asyncpg engine succeed on
the session pooler and fail on the transaction pooler, because the engine uses
prepared statements and transaction mode does not support them. The **direct**
connection (`db.<ref>.supabase.co`) is IPv6-only and unreachable from an EC2
instance, which has no IPv6 by default. So the host is
`aws-0-<region>.pooler.supabase.com:5432` and the user is `postgres.<project-ref>`.
Percent-encode special characters in the password (`#` becomes `%23`).

`GITAGUIDE_RATE_LIMIT_TRUSTED_PROXY_HOPS=1` (Caddy is the one proxy) is already
set. `GITAGUIDE_CORS_ORIGINS` must be your Vercel origin as a **JSON list**.

Then build and start. `GIT_SHA` is stamped into the image and reported by
`/health` as `build_sha`, so it is always possible to say which commit is
running; the smoke test fails if it is missing:

```bash
GIT_SHA=$(git rev-parse --short HEAD) docker compose --env-file .env.vm -f docker-compose.vm.yml up -d --build
docker compose --env-file .env.vm -f docker-compose.vm.yml logs -f api
```

The build takes 10-15 minutes on a t3.micro, less on a t3.small. **Check before
moving on.** The API log should reach, in order, the same lines DEPLOY.md step 3
lists (`safety gate self-test passed`, `database schema ensured`, `LLM chain`,
`rate limits`). Caddy answers 502 until then; that is the models loading, not a
fault. Then:

```bash
curl -s https://<your-hostname>/health
python -m scripts.prod_smoke https://<your-hostname>      # from your laptop
```

Every smoke check must pass. If Caddy cannot get a certificate, the log
(`docker compose ... logs caddy`) names why; nearly always it is port 80 not open
in the security group, or a hostname that does not resolve to this IP yet.

---

## Step 7: The frontend on Vercel

This is a **new project in the new Vercel account**, imported from
`github.com/shastrai2218/gitaguide`. Vercel cannot move a project between two
separate accounts, so the URL changes: the old `gitaguide-livid.vercel.app`
belongs to the old project. Keep the old project live until the new one is
verified.

**Import settings.** Vercel detects the backend at the repo root and gets it
wrong, so correct these on the import screen:

| Setting | Value |
| --- | --- |
| Root Directory | **`frontend`** (not `./`) |
| Application Preset | **Vite** (not FastAPI) |
| Build / Output / Install | leave alone; `frontend/vercel.json` supplies them |

**Environment variables.** Delete everything Vercel pre-filled from the root
`.env.example`. `GITAGUIDE_*`, `NVIDIA_API_KEY` and the database credentials
belong **only** in `.env.vm` on the instance, never in Vercel. Set just:

| NAME | Value |
| --- | --- |
| `VITE_API_BASE` | `https://<your-hostname>/api` (keep the `/api`) |
| `VITE_SHOW_META` | `1` |
| `VITE_CLERK_PUBLISHABLE_KEY` | only if DEPLOY.md step 4b accounts are on |

Do not set `VITE_MOCK`. The build fails without `VITE_API_BASE`; if the
instance does not exist yet, use `https://placeholder.invalid/api` to get a URL
and change it later. Vite inlines `VITE_*` at build time, so **changing it means
a redeploy**.

**Branch.** Vercel deploys the repo's default branch (`master`) to Production.
If the redesign is on `ui-ux-redesign` and unmerged, merge it, or set
Settings → Git → Production Branch.

**Order.** The instance needs the Vercel URL (for CORS) and Vercel needs the
instance's hostname. Either order works. Simplest:

1. Create the Vercel project with the placeholder API base; note its URL.
2. Do steps 1-6 above, with that URL in `GITAGUIDE_CORS_ORIGINS`.
3. Set the real `VITE_API_BASE` in Vercel and redeploy.
4. Walk the "Final check" list at the end of DEPLOY.md step 5. **No CORS errors
   in the browser console.**

## Step 8: Cut over and clean up

- If the old URL is still in `GITAGUIDE_CORS_ORIGINS`, remove it, then
  `docker compose --env-file .env.vm -f docker-compose.vm.yml up -d`.
- Delete the old Vercel project, and delete the service in the Render dashboard.
  The Render blueprint (`render.yaml`), the keep-warm workflow and the Render env
  template were removed from the repo once the API had moved; they remain in git
  history if a rollback is ever needed.
- Remove what Render left on the GitHub side: Settings → Applications →
  Installed GitHub Apps → Render → Configure → uninstall (or remove this
  repository from its access), and delete the `GITAGUIDE_HEALTH_URL` variable under
  Settings → Secrets and variables → Actions → Variables, which nothing uses now.
- Send testers the new link. **Their browser state does not follow them.**
  Anonymous chats and the consent record live in `localStorage`, which is per
  origin, so each tester starts clean and sees the consent gate again. Only
  signed-in (Clerk) chats sync across.
- Update the URL wherever the repo pins it: `README.md`,
  `docs/tester-pack/ONBOARDING.md`, `docs/kg_measurements.md`,
  `.env.vm.example`, and the constants in `tests/test_deploy_config.py` and
  `tests/test_tester_pack.py`. The tests exist to catch this drift, so they fail
  until all of them agree.

---

## Day to day

```bash
# GIT_SHA is read at build time and shows up in /health as build_sha.
dc() { GIT_SHA=$(git rev-parse --short HEAD) docker compose --env-file .env.vm -f docker-compose.vm.yml "$@"; }

dc ps                          # both containers Up
dc logs -f api                 # SAFETY_AUDIT, TURN_LATENCY, startup lines
git pull && dc up -d --build   # deploy an update
dc up -d                       # re-read .env.vm after editing it (restart does not)
curl -s https://<your-hostname>/health | grep -o '"build_sha":"[^"]*"'   # which commit is live
```

- **Rate limits and telemetry counters** are in-process, so one API container
  only. Do not scale it to two.
- **Telemetry** lands in the `api-logs` volume and survives restarts, but
  PostgreSQL is still the durable record.
- **AuraDB Free still pauses after 3 days idle**; a gap in testing means
  resuming it in the Aura console.
- **Disk:** container logs are capped at 3 x 10 MB each. Watch `df -h`.

## If something is wrong

| Symptom | Cause |
| --- | --- |
| Caddy never gets a certificate | port 80 not open in the security group, or the hostname does not resolve to this IP |
| can SSH but the site times out | security group missing 80/443, or the Elastic IP is not associated |
| browser: mixed content / blocked | `VITE_API_BASE` is `http://` or was not redeployed after the change |
| browser: CORS error, `curl` works | `GITAGUIDE_CORS_ORIGINS` does not match the Vercel origin exactly, or is not a JSON list |
| API exits at boot with a JSON error mentioning `cors_origins` | the value is comma-separated; it must be `["https://..."]` |
| everything rate-limited as one client | `TRUSTED_PROXY_HOPS` is `0`, so the limit keys on Caddy's address |
| container restarts, `exit 137`, no traceback | out of memory: is swap on (`free -h`), and is `PIPELINE_SERIALISE` still `true`? |
| `retrieval_backend` is `in-memory-hybrid` | Neo4j URI or password wrong; see the DEPLOY.md table |
| answers arrive but are canned | `GITAGUIDE_MOCK_MODE` is not exactly `false` |
