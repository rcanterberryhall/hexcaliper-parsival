# Hexcaliper Squire — Software Design Document

## 1. Purpose

Squire is a personal ops intelligence layer that sits alongside Hexcaliper. It ingests items from Outlook, Slack, GitHub, Jira, and Microsoft Teams, runs each through a local LLM, and presents a unified action dashboard. No data leaves your infrastructure.

---

## 2. System Architecture

```mermaid
graph TD
    Browser["Browser\n(Vanilla JS/CSS)"]
    Nginx["nginx :8082\n(reverse proxy + static files)"]
    API["FastAPI :8001\n(page-api container)"]
    SQLite[("SQLite WAL\ndata/squire.db")]
    Ollama["Ollama\n(hexcaliper.com via CF Access)"]

    subgraph Connectors
        Slack["Slack API\n(OAuth user tokens)"]
        GitHub["GitHub REST API\n(PAT)"]
        Jira["Jira Cloud API\n(API token)"]
        Teams["MS Teams Graph API\n(OAuth)"]
    end

    subgraph Host Sidecars
        OutlookSidecar["outlook_sidecar.py\n(Windows, win32com)"]
        ThunderbirdSidecar["thunderbird_sidecar.py\n(Ubuntu, mbox)"]
    end

    Browser --> Nginx
    Nginx --> API
    API --> SQLite
    API --> Ollama
    API --> Slack
    API --> GitHub
    API --> Jira
    API --> Teams
    OutlookSidecar -->|"POST /ingest"| Nginx
    ThunderbirdSidecar -->|"POST /ingest"| Nginx
```

The API container is the single source of truth. nginx proxies all `/page/api/*` requests to it and serves the static frontend from `/page/`.

---

## 3. Module Map

| Module | Responsibility |
|--------|---------------|
| `app.py` | FastAPI routes, HTTP layer, background task dispatch, project/noise learning |
| `orchestrator.py` | Scan, re-analysis, and ingest pipeline execution; Ollama concurrency semaphore |
| `agent.py` | Prompt construction, Ollama call, JSON response parsing → `Analysis` |
| `db.py` | SQLite schema, connection management, all CRUD helpers |
| `graph.py` | Knowledge graph CRUD, context retrieval, GraphRAG scoring |
| `situation_manager.py` | Cross-source situation formation, LLM synthesis, score decay |
| `embedder.py` | Sentence-embedding centroids per project (all-MiniLM-L6-v2) |
| `correlator.py` | Heuristic item-to-situation matching (project, topic, author overlap) |
| `seeder.py` | Seed state machine (ingest → LLM proposal → review → apply → reanalyze) |
| `models.py` | Pydantic models: `RawItem`, `Analysis`, `ActionItem` |
| `config.py` | Environment variable loading and hot-reload helpers |
| `connector_*.py` | Source-specific fetch logic (one per connector) |

---

## 4. Data Flow

### 4.1 Scan pipeline (frontend-triggered)

```mermaid
sequenceDiagram
    participant UI as Browser
    participant API as app.py
    participant Orch as orchestrator.py
    participant Conn as connector_*.py
    participant Agent as agent.py
    participant Graph as graph.py
    participant Sit as situation_manager.py
    participant DB as db.py

    UI->>API: POST /scan {sources}
    API->>Orch: run_scan(sources) [background thread]
    API-->>UI: 200 {status: started}

    loop for each source
        Orch->>Conn: fetch()
        Conn-->>Orch: [RawItem, ...]
    end

    loop for each RawItem (serialised via semaphore)
        Orch->>Agent: analyze(item)
        Agent->>Graph: get_context(item) [GraphRAG hint]
        Graph-->>Agent: related items text
        Agent->>Ollama: POST /api/generate
        Ollama-->>Agent: JSON response
        Agent-->>Orch: Analysis
        Orch->>DB: upsert_item + insert_todo/intel
        Orch->>Graph: index_item(analysis)
        Orch->>Sit: form_situation(item_id) [background]
    end

    UI->>API: GET /scan/status [polling]
    API-->>UI: {progress, message}
```

### 4.2 Ingest pipeline (sidecar-triggered)

```mermaid
sequenceDiagram
    participant Sidecar as outlook_sidecar.py
    participant API as app.py
    participant Orch as orchestrator.py

    Sidecar->>API: POST /ingest {items: [...]}
    API->>API: deduplicate against items table
    API-->>Sidecar: {received: N, skipped: M}
    API->>Orch: process_ingest_items(new_raw) [BackgroundTask]
    Note over Orch: Same analyze → save → index → situation loop as scan
```

---

## 5. LLM Analysis Pipeline

```mermaid
flowchart TD
    Raw["RawItem\n(source, item_id, title, body, author, metadata)"]
    Pre["Pre-processing\n• Strip CAUTION banners\n• Detect passdown (regex)\n• Match sender → project hint\n• Check is_replied flag"]
    GraphCtx["GraphRAG context\ngraph.get_context() →\nup to 4 related items\nscored by edge weight × recency"]
    EmbCtx["Embedding hint\nembedder.get_project_hint() →\nsemantic cluster match"]
    Prompt["Prompt assembly\nUser context + projects + topics +\nnoise keywords + sender hint +\ngraph hint + embedding hint + item"]
    Ollama["Ollama /api/generate\ntemperature=0.1"]
    Parse["JSON parse + validate\n• Clamp priority to high/medium/low\n• Validate project_tag against config\n• Clear action_items if fyi/noise\n• Apply passdown override"]
    Analysis["Analysis object\ncategory, priority, task_type,\naction_items, information_items,\ngoals, key_dates, summary"]

    Raw --> Pre --> GraphCtx --> EmbCtx --> Prompt --> Ollama --> Parse --> Analysis
```

### 5.1 Category schema

| Category | `task_type` | Meaning |
|----------|-------------|---------|
| `task` | `reply` | Compose and send a reply |
| `task` | `review` | Read/review a document, PR, or ticket |
| `task` | `null` | General action not fitting either sub-type |
| `approval` | — | Needs explicit sign-off from the user |
| `fyi` | — | Informational; no action required |
| `noise` | — | Irrelevant; suppressed from main view |

### 5.2 Hierarchy tiers

| Tier | Meaning |
|------|---------|
| `user` | Directly addressed — name/email in To/CC, @mention, assignment |
| `project` | Related to an active project but not directly addressed |
| `topic` | Matches a watch topic from `FOCUS_TOPICS` |
| `general` | Everything else |

---

## 6. Knowledge Graph

### 6.1 Structure

```mermaid
erDiagram
    nodes {
        text node_id PK
        text node_type
        text label
        text properties
    }
    edges {
        int  id PK
        text src_id FK
        text dst_id FK
        text edge_type
        real weight
        text created_at
    }
    nodes ||--o{ edges : "src_id"
    nodes ||--o{ edges : "dst_id"
```

Node types: `item`, `person`, `project`, `conversation`

### 6.2 Edge types and weights

| Edge type | Weight | Created when |
|-----------|--------|--------------|
| `in_conversation` | 1.00 | Two Outlook items share a `ConversationID` |
| `in_situation` | 0.80 | Two items grouped into the same situation |
| `tagged_to` | 0.55 | Item tagged to a named project |
| `authored_by` | 0.40 | Item sent by the same person |

### 6.3 GraphRAG scoring

Each candidate related item is scored:

```
score = edge_weight × exp(−age_days × ln(2) / 14)
```

Half-life is 14 days. The top-N items (default 4) are formatted as a context block and injected into the LLM prompt for the current item. This lets the model reason about conversation threads and project workstreams across sources without re-scanning all history.

---

## 7. Situation Layer

```mermaid
flowchart TD
    Trigger["item saved\n_spawn_situation_task(item_id)"]
    Load["Load item + recent items\n(last 7 days, same project or source)"]
    Correlate["correlator.find_related()\n• Project tag overlap\n• Author overlap\n• Topic keyword overlap"]
    Existing["Existing situation\nfor item?"]
    Merge["Merge item into situation\n• Add item_id to item_ids\n• Recompute open_actions"]
    New["Create new situation\n• LLM synthesis: title + summary\n• Score = sum of item urgency scores"]
    Decay["Score decay thread\nevery 30 min:\nscore × 0.95 for idle situations"]

    Trigger --> Load --> Correlate --> Existing
    Existing -->|yes| Merge
    Existing -->|no| New
    Merge --> Decay
    New --> Decay
```

Situations group related items across sources into a single tracked event (e.g. "Platform Migration incident" pulling together Slack threads, GitHub PRs, and Jira tickets).

---

## 8. Seed Workflow State Machine

```mermaid
stateDiagram-v2
    [*] --> waiting_for_ingest : POST /seed
    waiting_for_ingest --> analyzing : items detected in DB
    analyzing --> review : LLM proposes projects + topics
    review --> reanalyzing : POST /seed/apply (user confirms)
    reanalyzing --> scan_prompt : all items re-analyzed
    scan_prompt --> scanning : POST /seed/scan
    scan_prompt --> done : POST /seed/skip_scan
    scanning --> done : scan complete
    done --> [*]
```

The seed workflow bootstraps Squire when first deployed or after adding new projects. It runs a map-reduce LLM pass over existing items to propose a project list, lets the user review and edit, then re-analyzes everything with the confirmed configuration.

---

## 9. Database Schema

```mermaid
erDiagram
    items {
        text item_id PK
        text source
        text direction
        text title
        text author
        text timestamp
        text category
        text task_type
        text priority
        text hierarchy
        text project_tag
        text situation_id FK
        text conversation_id
        text action_items
        text information_items
        text body_preview
    }

    todos {
        int  id PK
        text item_id FK
        text description
        text priority
        int  done
        text deadline
        int  is_manual
        text project_tag
    }

    intel {
        int  id PK
        text item_id FK
        text fact
        text relevance
        text project_tag
        int  dismissed
    }

    situations {
        text situation_id PK
        text title
        text summary
        text status
        text item_ids
        real score
        text project_tag
        int  dismissed
    }

    embeddings {
        int  id PK
        text project
        text items
        text centroids
    }

    items ||--o{ todos : "item_id"
    items ||--o{ intel : "item_id"
    items }o--|| situations : "situation_id"
```

---

## 10. Project Learning

When a user tags an item to a project (`POST /analyses/{item_id}/tag`), a background job:

1. Updates `project_tag` on the analysis record.
2. Calls the LLM to extract 5–10 characteristic keywords from the item.
3. Merges keywords into `project.learned_keywords` (capped at 100).
4. Extracts all email addresses from From/To/CC fields.
5. Merges addresses into `project.learned_senders` (capped at 50, excluding the user's own address).
6. Saves updated settings and hot-reloads config.

On the next scan, learned keywords appear in the LLM prompt and in Slack/Teams pre-filters. Learned senders trigger deterministic pre-tagging before the LLM runs.

---

## 11. Noise Learning

When a user marks an item as noise (`POST /analyses/{item_id}/noise`), a background job:

1. Sets `category="noise"`, `priority="low"`, `has_action=false`.
2. Extracts keywords via LLM.
3. Merges into `config.NOISE_KEYWORDS` (capped at 200).

On subsequent scans, items matching only noise keywords are silently skipped or pre-labelled by the LLM.

---

## 12. Outlook Sidecar

```mermaid
flowchart LR
    Outlook["Outlook client\n(Windows, MAPI)"]
    Sidecar["outlook_sidecar.py\n• Inbox (ReceivedTime)\n• Sent Items (SentOn)"]
    Normalize["Normalise\n• Strip Re:/Fwd: from subject\n• Truncate body to 3000 chars\n• Collapse blank lines\n• Extract To/CC recipients"]
    Post["POST /ingest\n(batches of 50)\nCF-Access headers"]
    API["Squire API"]

    Outlook -->|win32com| Sidecar --> Normalize --> Post --> API
```

The sidecar fetches both Inbox and Sent Items, tagging each with `direction: received|sent` and `conversation_id`/`conversation_topic` for graph threading. Cloudflare Access credentials are stored in Windows Credential Manager via `keyring`.

---

## 13. Concurrency Model

```mermaid
flowchart TD
    Scan["run_scan() thread"]
    Reanalyze["run_reanalyze() thread"]
    Ingest["process_ingest_items() BackgroundTask"]
    Seed["seed state machine thread"]
    Sit["situation_manager thread pool"]
    Decay["score_decay_loop() thread"]

    Sem["threading.Semaphore(1)\n_sem — Ollama calls serialised"]
    Lock["threading.Lock()\ndb.lock — SQLite writes serialised"]

    Scan -->|acquires| Sem
    Reanalyze -->|acquires| Sem
    Ingest -->|acquires| Sem
    Seed -->|acquires| Sem

    Scan -->|acquires| Lock
    Reanalyze -->|acquires| Lock
    Ingest -->|acquires| Lock
    Sit -->|acquires| Lock
    Decay -->|acquires| Lock
```

Only one Ollama call runs at a time. SQLite WAL mode allows concurrent reads while writes are serialised through `db.lock`. Scan/reanalyze/ingest respect a shared `scan_state["cancelled"]` flag and exit cleanly after their current item.
