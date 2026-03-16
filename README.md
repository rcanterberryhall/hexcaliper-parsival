# Hexcaliper Squire

A companion service for [Hexcaliper](https://github.com/rcanterberryhall/hexcaliper) that consolidates responsibilities from Outlook, Slack, GitHub, and Jira into a single ops dashboard. Uses the Hexcaliper Ollama instance to extract action items, priority, goals, key dates, and context-aware relevance signals — no data leaves your infrastructure.

## Architecture

```
Browser (/page/)
  └── nginx (:8082)
        └── /page/api/* → FastAPI/uvicorn (:8001, service: page-api)
                            ├── Ollama (hexcaliper.com via Cloudflare Access)
                            ├── Slack API
                            ├── GitHub API
                            ├── Jira Cloud API
                            └── TinyDB  (./data/page.db)

Email ingestion (host, not Docker):
  Windows  → scripts/outlook_sidecar.py      (win32com)
  Ubuntu   → scripts/thunderbird_sidecar.py  (local mbox/Maildir)
    └── POST /page/api/ingest → API
```

Runs alongside the existing Hexcaliper stack. Does not conflict with hexcaliper's ports (8080/8000).

| Port | Service                                    |
|------|--------------------------------------------|
| 8001 | FastAPI API (internal, bridge network)     |
| 8082 | nginx (web UI + API proxy, host-exposed)   |

| Component  | Technology                                                  |
|------------|-------------------------------------------------------------|
| Frontend   | Vanilla JS + CSS, served by nginx                           |
| API        | Python 3.12, FastAPI, uvicorn                               |
| Storage    | TinyDB (flat JSON — same as Hexcaliper)                     |
| LLM        | Ollama via Hexcaliper appliance (Cloudflare Access)         |
| Networking | Bridge (`app` network) — same pattern as Hexcaliper         |

## Connectors

| Source      | How                                | What it pulls                                       |
|-------------|------------------------------------|-----------------------------------------------------|
| Outlook     | Host sidecar script (win32com)     | Recent inbox emails with To/CC recipients           |
| Thunderbird | Host sidecar script (mbox/Maildir) | Recent inbox emails with To/CC recipients           |
| Slack       | Per-user OAuth tokens              | @mentions, DMs, relevant channel messages           |
| GitHub      | PAT REST API                       | Notifications, assigned issues, PR review requests  |
| Jira        | API token REST API                 | Open tickets assigned to current user               |

## Prerequisites

- Hexcaliper running with Ollama accessible at your configured endpoint
- A Cloudflare Access service token for the Ollama application
- Docker and Docker Compose (same versions as Hexcaliper)

## Setup

```bash
git clone <repo> hexcaliper-squire
cd hexcaliper-squire

# Edit docker-compose.yml and fill in your credentials
# (CTRL+F for "your-" to find all placeholders)

docker compose up --build -d

# Open in browser
xdg-open http://localhost:8082/page/
```

## Configuration

All credentials can be set in `docker-compose.yml` under the `page-api` environment block, or saved via the Settings page in the UI (which hot-reloads config without a container restart).

### Core settings

| Variable           | Description                                                                      |
|--------------------|----------------------------------------------------------------------------------|
| `CF_CLIENT_ID`     | Cloudflare Access service token ID                                               |
| `CF_CLIENT_SECRET` | Cloudflare Access service token secret                                           |
| `OLLAMA_URL`       | Ollama API endpoint (default: `https://ollama.hexcaliper.com/api/generate`)      |
| `OLLAMA_MODEL`     | Model for extraction (default: `llama3.2`)                                       |
| `LOOKBACK_HOURS`   | Hours of history per scan (default: `48`)                                        |

### User context

These fields are passed directly into every LLM prompt and are also used by the Slack connector for pre-filtering.

| Variable       | Description                                                                                   |
|----------------|-----------------------------------------------------------------------------------------------|
| `USER_NAME`    | Your display name (e.g. `Jane Smith`)                                                         |
| `USER_EMAIL`   | Your email address (e.g. `jane.smith@company.com`)                                            |
| `FOCUS_TOPICS` | Comma-separated general watch-topic keywords (e.g. `kubernetes,cost reduction`)               |
| `PROJECTS`     | JSON array of project objects — see [Project configuration](#project-configuration) below     |

### Slack

| Variable              | Description                                                                          |
|-----------------------|--------------------------------------------------------------------------------------|
| `SLACK_CLIENT_ID`     | Slack app Client ID (for OAuth)                                                      |
| `SLACK_CLIENT_SECRET` | Slack app Client Secret                                                              |
| `SLACK_BOT_TOKEN`     | Legacy bot token (`xoxb-...`) — only used if no user tokens are connected            |
| `SLACK_CHANNELS`      | Comma-separated channel names for the legacy bot path. Empty = all joined channels   |

Connect your Slack workspaces via the Settings page (OAuth flow) to use per-user tokens instead of a bot token.

Required OAuth user scopes: `channels:history` `channels:read` `groups:history` `groups:read` `im:history` `im:read` `mpim:history` `mpim:read` `search:read` `users:read`

### GitHub

| Variable          | Description                                        |
|-------------------|----------------------------------------------------|
| `GITHUB_PAT`      | GitHub PAT (scopes: `repo`, `notifications`)       |
| `GITHUB_USERNAME` | Your GitHub username                               |

### Jira

| Variable      | Description                                                                         |
|---------------|-------------------------------------------------------------------------------------|
| `JIRA_EMAIL`  | Jira account email                                                                  |
| `JIRA_TOKEN`  | Jira API token (https://id.atlassian.com/manage-profile/security/api-tokens)        |
| `JIRA_DOMAIN` | `yourco.atlassian.net`                                                              |
| `JIRA_JQL`    | JQL for your tickets (default: assignee = currentUser() AND statusCategory != Done) |

### Cloudflare Access service token

Zero Trust → **Access → Service Auth → Service Tokens** → Create token.
Copy Client ID and Client Secret (shown once). Add the token to the Access Policy protecting your Ollama application.

## Project configuration

Projects let Squire associate items with named workstreams. Each project is a JSON object with the following keys:

| Key                | Type             | Description                                                                                       |
|--------------------|------------------|---------------------------------------------------------------------------------------------------|
| `name`             | string           | Project name used for tagging and display                                                         |
| `keywords`         | array of strings | Manually curated keywords — items matching these are tagged to this project                       |
| `channels`         | array of strings | Slack channel names monitored for this project                                                    |
| `learned_keywords` | array of strings | Keywords learned via the tagging workflow (see [Project learning](#project-learning))             |
| `learned_senders`  | array of strings | Email addresses learned via tagging — senders/groups associated with this project                 |

Example `PROJECTS` value (set as an env var or saved via Settings):

```json
[
  {
    "name": "Platform Migration",
    "keywords": ["k8s", "migration", "eks"],
    "channels": ["platform-eng", "infra-alerts"],
    "learned_keywords": [],
    "learned_senders": []
  }
]
```

## Context hierarchy

Every analysed item is assigned a `hierarchy` value indicating how directly it relates to you:

| Tier      | Meaning                                                                                         |
|-----------|-------------------------------------------------------------------------------------------------|
| `user`    | Directly addressed to you — your name/email in To/CC, a Slack DM or @mention, or an assignment  |
| `project` | Related to one of your active projects but not directly addressed to you                        |
| `topic`   | Matches a watch topic from `FOCUS_TOPICS` but not a specific project                            |
| `general` | Everything else                                                                                 |

The LLM assigns hierarchy based on prompt rules. The Slack connector pre-computes hierarchy during channel pre-filtering and passes it as a hint in `item.metadata`.

## Project learning

When you tag an item to a project via `POST /analyses/{item_id}/tag`, Squire:

1. Immediately updates the stored analysis with the new `project_tag`.
2. Calls the LLM in the background to extract 5–10 characteristic keywords from the item's title and body preview.
3. Merges the new keywords (lowercased) into the project's `learned_keywords` list (capped at 100 entries).
4. Extracts all email addresses from the item's sender (`From`) and recipient (`To`/`CC`) fields.
5. Merges those addresses into the project's `learned_senders` list (capped at 50 entries), excluding your own address.
6. Saves the updated settings to TinyDB and hot-reloads config.

On the next scan, learned keywords are included in both the LLM prompt and the Slack pre-filter. Learned senders are checked deterministically before the LLM runs — if the incoming item's sender or any recipient address matches a `learned_senders` entry, the project tag is applied automatically. This covers both individual contacts who regularly email about a project and shared distribution lists that receive project-related traffic.

## Noise filter

When you mark an item as noise via `POST /analyses/{item_id}/noise`, Squire:

1. Immediately sets `category="noise"`, `priority="low"`, and `has_action=false` on the stored analysis.
2. Calls the LLM in the background to extract keywords from the item.
3. Merges the keywords into `NOISE_KEYWORDS` (capped at 200 entries) and saves/reloads settings.

On subsequent Slack scans, messages that match only noise keywords (and no positive user/project/topic signal) are silently skipped. The LLM prompt also lists noise keywords so the model can set `category="noise"` for matching email and GitHub items.

## Passdown detection

Shift passdown / handoff notes are detected deterministically before the LLM runs:

- The subject line or first 300 characters of the body contains the word **"passdown"**, OR
- The opening lines contain a phrase matching **"notes from \<word\> shift"** (e.g. "notes from 2nd shift", "notes from first shift").

When either pattern matches, `is_passdown` is forced to `true` regardless of the LLM response. Passdown items receive `has_action=false` by default unless the content explicitly directs an action at you by name.

## Email ingestion (sidecar scripts)

Email is fed into the API from the host machine — both Outlook (win32com) and Thunderbird (local mbox) require local client state not available inside Docker.  Both sidecars include `to` and `cc` fields in item metadata so the LLM can determine whether you are a direct recipient, a CC recipient, or absent from the header entirely.

### Windows — Outlook

```bash
pip install requests pywin32 keyring
python scripts/outlook_sidecar.py --setup   # first-time credential setup
python scripts/outlook_sidecar.py           # normal / scheduled run
```

Cloudflare Access credentials are stored in Windows Credential Manager via `keyring` and never written to disk. Run `--setup` once to store them, then schedule the normal run with Windows Task Scheduler every 30–60 minutes.

### Ubuntu — Thunderbird

```bash
pip install requests
python scripts/thunderbird_sidecar.py
```

Thunderbird must keep messages locally:
**Account Settings → Synchronization & Storage → Keep messages for this account on this computer**

Add to crontab:
```
*/30 * * * * python3 /path/to/scripts/thunderbird_sidecar.py >> /tmp/page-sidecar.log 2>&1
```

Both sidecars POST to `/page/api/ingest`. The API deduplicates by message ID so re-running is safe.

## API endpoint reference

Interactive docs: `http://localhost:8001/docs`

### Health and settings

| Method   | Path        | Description                                                                                                                  |
|----------|-------------|------------------------------------------------------------------------------------------------------------------------------|
| `GET`    | `/health`   | Health check and config warnings                                                                                             |
| `GET`    | `/settings` | Current settings (secrets masked). Returns all config and credential fields                                                  |
| `POST`   | `/settings` | Persist and hot-reload settings. Fields containing `•` (masked) are ignored                                                  |

### Scanning

| Method | Path           | Description                                                                    |
|--------|----------------|--------------------------------------------------------------------------------|
| `POST` | `/scan`        | Start a scan (`{"sources": ["slack","github","jira","outlook"]}`)              |
| `GET`  | `/scan/status` | Poll scan progress and current item                                            |
| `POST` | `/ingest`      | Receive raw items from sidecar scripts; deduplicates and queues AI analysis    |
| `POST` | `/reset`       | Truncate analyses, todos, and scan logs (settings are preserved)               |

### Analyses

| Method | Path                        | Description                                                                        |
|--------|-----------------------------|------------------------------------------------------------------------------------|
| `GET`  | `/analyses`                 | All analysed items, newest first (params: `source`, `category`). Returns up to 200 |
| `POST` | `/analyses/{item_id}/tag`   | Tag item to a project; triggers background keyword learning                        |
| `POST` | `/analyses/{item_id}/noise` | Mark item as irrelevant; triggers background noise keyword learning                |

### Projects

| Method | Path        | Description                                                                          |
|--------|-------------|--------------------------------------------------------------------------------------|
| `GET`  | `/projects` | List configured projects with manual keywords, channels, and learned keyword counts  |

### Todos

| Method   | Path          | Description                                                                |
|----------|---------------|----------------------------------------------------------------------------|
| `GET`    | `/todos`      | List action items (`source`, `priority`, `done`). Sorted by priority       |
| `PATCH`  | `/todos/{id}` | Update a todo (`{"done": true}`)                                           |
| `DELETE` | `/todos/{id}` | Delete a todo                                                              |

### Stats

| Method | Path     | Description                                                                 |
|--------|----------|-----------------------------------------------------------------------------|
| `GET`  | `/stats` | Aggregate counts, open todos by source, items by category, last scan info   |

### Slack OAuth

| Method   | Path                          | Description                                                        |
|----------|-------------------------------|--------------------------------------------------------------------|
| `GET`    | `/slack/connect`              | Redirect to Slack OAuth authorisation page                         |
| `GET`    | `/slack/callback`             | OAuth callback — exchanges code for user token, saves to settings  |
| `GET`    | `/slack/workspaces`           | List connected workspaces (team name and ID)                       |
| `DELETE` | `/slack/workspaces/{team_id}` | Disconnect a workspace                                             |

## Data persistence

Stored in `./data/page.db` (TinyDB JSON). Bind-mounted and survives restarts.

```bash
docker compose down
rm data/page.db   # wipe all data
docker compose up -d
```

Alternatively, use `POST /reset` to clear analyses, todos, and scan logs while keeping your saved settings.
