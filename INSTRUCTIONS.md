# Hexcaliper Squire — User Guide

## Table of Contents

1. [First-time setup](#1-first-time-setup)
2. [The seed workflow](#2-the-seed-workflow)
3. [Connecting sources](#3-connecting-sources)
4. [Running a scan](#4-running-a-scan)
5. [Understanding the dashboard](#5-understanding-the-dashboard)
6. [Managing todos](#6-managing-todos)
7. [Reviewing analyses](#7-reviewing-analyses)
8. [Situations](#8-situations)
9. [Intel](#9-intel)
10. [Project configuration](#10-project-configuration)
11. [Settings reference](#11-settings-reference)
12. [Outlook sidecar (Windows)](#12-outlook-sidecar-windows)
13. [Thunderbird sidecar (Ubuntu)](#13-thunderbird-sidecar-ubuntu)
14. [Re-analysis](#14-re-analysis)
15. [Maintenance](#15-maintenance)

---

## 1. First-time setup

### Deploy the stack

```bash
git clone <repo> hexcaliper-squire
cd hexcaliper-squire
```

Edit `docker-compose.yml`. Search for `your-` to find every placeholder:

| Placeholder | What to fill in |
|-------------|-----------------|
| `your-ollama-url` | Full URL to your Ollama endpoint, e.g. `https://ollama.hexcaliper.com/api/generate` |
| `your-cf-client-id` | Cloudflare Access service token Client ID |
| `your-cf-client-secret` | Cloudflare Access service token Client Secret |
| `your-name` | Your display name, e.g. `Jane Smith` |
| `your-email` | Your email address |

Everything else (Slack, GitHub, Jira, etc.) can be left blank initially and filled in later via the Settings page.

```bash
docker compose up --build -d
open http://localhost:8082/page/
```

The stack is ready when the browser loads the dashboard.

### Fix data directory permissions (first run only)

Docker creates `data/` as root on the first run. If you need to run host-side scripts (migration, sidecars) before bringing the stack up, fix ownership first:

```bash
docker compose down
sudo chown -R $(whoami):$(whoami) data/
```

---

## 2. The seed workflow

The seed workflow is the recommended starting point when you have existing email, Slack, or GitHub data you want Squire to learn from. It discovers your projects automatically rather than requiring you to configure them manually upfront.

### Step 1 — Ingest existing data

Before starting the seed, push some historical data into Squire:

- **Outlook**: run the sidecar in seed mode (fetches last 30 days):
  ```bash
  python scripts/outlook_sidecar.py --seed
  ```
- **Other sources**: use the Settings page to add credentials, then run a manual scan.

### Step 2 — Start the seed job

Click **Seed** in the navigation or call `POST /seed` with an optional context string:

```json
{
  "context": "I work on platform infrastructure. Key projects are cloud migration, cost reduction, and on-call tooling."
}
```

The context is passed to the LLM and helps it disambiguate similar project names. You can update it while the job is running via `PATCH /seed/context`.

### Step 3 — Review proposals

When the state reaches `review`, the UI displays the LLM's proposed project list and focus topics. Edit names, merge duplicates, and delete anything irrelevant before confirming.

### Step 4 — Apply

Click **Apply** (`POST /seed/apply`). Squire will:
1. Save the confirmed projects and topics to settings.
2. Re-tag all stored items by keyword match.
3. Build semantic embeddings for each project.
4. Re-analyze all items with the full context.

### Step 5 — Scan (optional)

After re-analysis completes, the UI prompts you to run a live scan to pull fresh items. Click **Scan** or **Skip** to go straight to the dashboard.

---

## 3. Connecting sources

### GitHub

1. Go to **Settings → GitHub**.
2. Paste a PAT with `repo` and `notifications` scopes.
3. Enter your GitHub username.
4. Save.

### Jira

1. Go to **Settings → Jira**.
2. Enter your Atlassian email, an API token from `id.atlassian.com/manage-profile/security/api-tokens`, and your domain (e.g. `yourco.atlassian.net`).
3. Optionally customise the JQL query (default: `assignee = currentUser() AND statusCategory != Done`).
4. Save.

### Slack

1. Go to **Settings → Slack** and click **Connect Slack workspace**.
2. Authorise the OAuth flow in your browser.
3. Repeat for additional workspaces.

Required OAuth user scopes: `channels:history` `channels:read` `groups:history` `groups:read` `im:history` `im:read` `mpim:history` `mpim:read` `search:read` `users:read`

### Microsoft Teams

1. Go to **Settings → Teams** and click **Connect Teams account**.
2. Authorise via the Microsoft identity platform.
3. Repeat for additional tenants.

### Outlook / Thunderbird

See [section 12](#12-outlook-sidecar-windows) and [section 13](#13-thunderbird-sidecar-ubuntu).

---

## 4. Running a scan

Click **Scan** in the top navigation. Select which sources to include and click **Start Scan**.

The scan runs in the background. A status bar shows the current source and item being processed. The dashboard updates live as results arrive.

To cancel mid-scan, click **Stop**. Items already analysed are kept; the scan log records the partial run.

**Tip:** Scans deduplicate by `item_id`, so running the same scan twice is safe.

---

## 5. Understanding the dashboard

### Item badges

Each item in the Analyses list shows a coloured badge indicating its category:

| Badge | Colour | Meaning |
|-------|--------|---------|
| `task · reply` | Red | You need to compose a reply |
| `task · review` | Blue | You need to read/review something |
| `task` | Orange | General action item |
| `approval` | Purple | Needs your sign-off |
| `fyi` | Grey | Informational only |
| `noise` | Light grey | Filtered out (visible when showing all) |

### Priority

Items are sorted high → medium → low within each category. Priority is assigned by the LLM but can be overridden per-item.

### Hierarchy

The `hierarchy` field indicates how directly the item relates to you:

| Tier | What it means |
|------|---------------|
| `user` | You are in To/CC, @mentioned, or directly assigned |
| `project` | Related to one of your projects |
| `topic` | Matches a watch topic |
| `general` | Catch-all |

### Filters

Use the filter bar above the list to narrow by source, category, priority, or project tag.

---

## 6. Managing todos

### Viewing todos

The **Todos** tab shows all open action items extracted from analysed items, sorted by priority (high → medium → low).

Use query parameters to filter:
- `?done=true` — include completed items
- `?source=slack` — filter by source
- `?priority=high` — filter by priority

### Marking complete

Click the checkbox next to a todo or use the **✓** button. The item moves to the completed list.

### Editing a todo

Click the **✎** (pencil) button on a todo row to edit:
- Description
- Deadline
- Priority
- Project tag

### Creating a manual todo

Click **+ Add action** at the top of the Todos panel. Fill in:

| Field | Required | Notes |
|-------|----------|-------|
| Description | Yes | Free text — what needs doing |
| Priority | No | Defaults to `medium` |
| Deadline | No | ISO date `YYYY-MM-DD` |
| Project | No | Must match a configured project name |

Manual todos are flagged with `is_manual=1` and are not affected by re-analysis.

### Deleting a todo

Click the **✕** button on a todo row. This permanently removes the row.

---

## 7. Reviewing analyses

### Opening an item

Click any row in the Analyses list to open the detail panel. This shows:
- Full summary
- Action items
- Goals and key dates extracted by the LLM
- Intel items (key facts)
- Source metadata (author, To/CC, timestamp)

### Editing an analysis

In the detail panel, click **Edit** to change:

| Field | Notes |
|-------|-------|
| Category | `task`, `approval`, `fyi`, or `noise` |
| Task type | `reply`, `review`, or none (only relevant for `task`) |
| Priority | `high`, `medium`, or `low` |
| Project tag | Must match a configured project name |

Changing `category` to `noise` immediately removes all associated todos and clears `has_action`. Changing `priority` syncs to all associated todo rows.

### Tagging an item to a project

Open the item, click **Tag to project**, and select a project. This:
1. Updates the `project_tag` on the analysis record.
2. Triggers background keyword and sender learning for that project.

On subsequent scans, items from the same sender or matching the learned keywords will be auto-tagged.

### Marking as noise

Click **Mark as noise** on any item. This:
1. Sets `category="noise"`, `priority="low"`, `has_action=false`.
2. Removes associated todos.
3. Triggers background keyword extraction into the noise filter.

Future items matching these keywords will be pre-labelled as noise by the LLM.

---

## 8. Situations

Situations are automatically-formed cross-source clusters. When multiple items relate to the same event or workstream (e.g. a production incident generating Slack messages, GitHub issues, and Jira tickets), Squire groups them into a single situation with a composite urgency score.

### Viewing situations

Open the **Situations** tab. Items are sorted by score descending (most urgent first). Each situation shows:
- LLM-generated title and summary
- Composite urgency score
- Contributing sources
- Open action items (union of all constituent todos)

### Filtering situations

Use the filter bar to narrow by `project`, `status`, or `min_score`.

### Editing a situation

Click a situation to open it, then click **Edit** to update the title, status, or project tag.

Status values: `in_progress`, `monitoring`, `resolved`

### Dismissing a situation

Click **Dismiss** to hide a situation from the main view. An optional reason can be recorded. Dismissed situations are retrievable by passing `?dismissed=true` to `GET /situations`.

### Manually rescoring

Click **Rescore** to trigger immediate score recomputation and LLM re-synthesis for a situation (useful after merging new items or editing constituent analyses).

---

## 9. Intel

Intel items are key facts and completed-action notes extracted by the LLM that are worth knowing but are not action items for you — e.g. "Server was rebooted at 03:00", "PR #421 was merged by Alice".

### Viewing intel

Open the **Intel** tab. Filter by source or project tag.

### Dismissing intel

Click **Dismiss** on an intel row to hide it. Unlike situations, dismissed intel is permanently hidden (use `DELETE /intel/{id}` to remove it entirely).

---

## 10. Project configuration

Projects are the core of Squire's relevance engine. Each project acts as a named workstream that items can be tagged to.

### Adding a project

Go to **Settings → Projects** and add a project object:

```json
{
  "name": "Platform Migration",
  "description": "Kubernetes migration from on-prem to EKS; owned by the platform team.",
  "keywords": ["k8s", "migration", "eks", "node pool"],
  "channels": ["platform-eng", "infra-alerts"],
  "senders": ["platform-team@company.com"],
  "parent": "",
  "learned_keywords": [],
  "learned_senders": []
}
```

| Field | Purpose |
|-------|---------|
| `name` | Exact name used in tagging — the LLM must use this verbatim |
| `description` | Passed to the LLM to disambiguate projects with similar names |
| `keywords` | Manually curated; items containing these are tagged to this project |
| `channels` | Slack/Teams channels monitored for this project |
| `senders` | Email addresses or group aliases associated with this project |
| `parent` | Optional parent project name for sub-project relationships |
| `learned_keywords` | Auto-populated by the tagging workflow — do not edit by hand |
| `learned_senders` | Auto-populated by the tagging workflow — do not edit by hand |

### Sub-projects

Set `parent` to an existing project name to create a hierarchy. The LLM prompt notes the sub-project relationship and will prefer the most specific match.

### Removing a project

Delete the project object from the `PROJECTS` array in Settings and save. Existing items tagged to the project retain their `project_tag` value but will no longer receive learned-keyword updates.

---

## 11. Settings reference

Access via **Settings** in the navigation. All fields can be set here or in `docker-compose.yml`. Changes take effect immediately without a container restart.

### Core

| Field | Description |
|-------|-------------|
| Ollama URL | Full endpoint for your Ollama instance |
| Ollama model | Model name, e.g. `llama3.2`, `mistral:7b` |
| Lookback hours | How many hours of history each scan pulls (default: 48) |

### Identity

| Field | Description |
|-------|-------------|
| Your name | Used in every LLM prompt and in Slack/Teams pre-filtering |
| Your email | Used to identify direct-address items |
| Focus topics | Comma-separated keywords — items matching these get `hierarchy=topic` |

### Secrets

Secrets are displayed masked after first entry. Submitting a masked value (containing `•`) leaves the original intact — you only need to re-enter a secret if you are changing it.

| Field | Notes |
|-------|-------|
| CF Access Client ID | Cloudflare Access service token for Ollama |
| CF Access Client Secret | Cloudflare Access service token secret |
| Slack Client ID / Secret | Slack app OAuth credentials |
| Slack Bot Token | Legacy path only — prefer OAuth |
| GitHub PAT | `repo` + `notifications` scopes required |
| Jira Email / Token | Atlassian API token |
| Teams Client ID / Secret | Azure AD app credentials |

---

## 12. Outlook sidecar (Windows)

The Outlook sidecar reads from the local Outlook client via `win32com` and POSTs to Squire. It must run on the Windows machine where Outlook is installed.

### Install dependencies

```powershell
pip install requests pywin32 keyring
```

### First-time credential setup

```powershell
python scripts\outlook_sidecar.py --setup
```

Enter your Cloudflare Access Client ID and Client Secret when prompted. They are stored in Windows Credential Manager and never written to disk.

### Normal run

```powershell
python scripts\outlook_sidecar.py
```

Fetches the last 48 hours from Inbox and Sent Items and POSTs to Squire.

### Seed run (historical import)

```powershell
python scripts\outlook_sidecar.py --seed
```

Fetches the last 30 days (up to 500 emails). Run once after initial setup before starting the seed workflow.

### Scheduling

Use Windows Task Scheduler to run the normal command every 30–60 minutes:

1. Open **Task Scheduler → Create Basic Task**.
2. Set trigger to **Daily**, repeat every 30 minutes.
3. Set action to `python.exe` with argument `C:\path\to\scripts\outlook_sidecar.py`.

### What the sidecar sends

Each email item includes:

| Field | Notes |
|-------|-------|
| `item_id` | Outlook `EntryID` — used for deduplication |
| `title` | Subject line |
| `body` | Body text, truncated to 3000 characters, blank lines collapsed |
| `author` | `Name <email>` |
| `metadata.direction` | `received` or `sent` |
| `metadata.conversation_id` | Outlook `ConversationID` — used for graph threading |
| `metadata.conversation_topic` | Normalised subject (Re:/Fwd: stripped) |
| `metadata.is_read` | Whether the email has been read |
| `metadata.to` / `metadata.cc` | Recipient lists |
| `metadata.is_replied` | Whether you replied |

---

## 13. Thunderbird sidecar (Ubuntu)

### Install dependencies

```bash
pip install requests
```

### Configure Thunderbird

Thunderbird must store messages locally:

**Account Settings → Synchronization & Storage → Keep messages for this account on this computer**

### Run

```bash
python3 scripts/thunderbird_sidecar.py
```

### Schedule

Add to crontab to run every 30 minutes:

```bash
crontab -e
```

```
*/30 * * * * python3 /path/to/scripts/thunderbird_sidecar.py >> /tmp/squire-sidecar.log 2>&1
```

---

## 14. Re-analysis

Re-analysis re-runs the LLM on all stored items using the current settings. Use it after:
- Adding new projects or keywords
- Changing your user name or email
- Updating the Ollama model
- Migrating from TinyDB to SQLite (backfills graph edges and new category schema)

### Trigger re-analysis

Click **Re-analyze** in the navigation, or:

```bash
curl -X POST http://localhost:8082/page/api/reanalyze
```

Poll progress via:

```bash
curl http://localhost:8082/page/api/scan/status
```

Re-analysis respects the same concurrency rules as a scan — you cannot start one if a scan or another re-analysis is already running (returns `409`).

### Count before running

Check how many items will be processed:

```bash
curl http://localhost:8082/page/api/reanalyze/count
```

---

## 15. Maintenance

### Wipe all data (keep settings)

```bash
curl -X POST http://localhost:8082/page/api/reset
```

Truncates `items`, `todos`, `intel`, `situations`, `scan_logs`, and `embeddings`. Settings are preserved.

### Wipe everything

```bash
docker compose down
rm data/squire.db
docker compose up -d
```

### Migrate from TinyDB (one-time)

If you have a `data/page.db` from an older deployment:

```bash
docker compose down
sudo chown -R $(whoami):$(whoami) data/
python3 scripts/migrate_to_sqlite.py
docker compose up -d
```

Then run a re-analysis to backfill graph edges and the new category schema.

### View logs

```bash
docker compose logs page-api -f
```

### Interactive API docs

```
http://localhost:8082/page/api/docs
```

FastAPI's Swagger UI lists every endpoint with request/response schemas.

### Health check

```bash
curl http://localhost:8082/page/api/health
```

Returns `{"ok": true, "warnings": [...]}`. Warnings list any missing credentials or configuration issues.
