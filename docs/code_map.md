# Parsival code map

## How to read this

Parsival is a single-package project: all backend source lives in one flat
package, `api/`, with no subpackages. The package table below therefore has
exactly one row, and the useful orientation is the **layer map** in this
section. [`api/README.md`](../api/README.md) carries the full generated
public-API listing for all 23 modules; the repository
[`README.md`](../README.md) is the feature and configuration reference.

The service is a pipeline. Items arrive from connectors or host sidecars, are
filtered, analysed by an LLM, stored in SQLite, and then interpreted into
situations and a learned attention ranking:

```
connectors / sidecars → orchestrator → noise_filter → agent ─→ llm → merLLM/Ollama/Claude
                                                        │
                                          embedder + graph (prompt context)
                                                        ↓
                                                       db  (SQLite WAL)
                                                        ↓
                              correlator → situation_manager → attention
                                                        ↓
                                                       app  (107 HTTP routes)
```

Read the modules in dependency order — each layer depends only on the ones
above it:

| Layer | Modules | What it provides |
|---|---|---|
| Foundation | `config`, `models`, `crypto` | Hot-reloadable settings, shared dataclasses, Fernet credential encryption |
| Storage | `db`, `graph` | SQLite WAL access behind a re-entrant lock; the knowledge graph used for GraphRAG context |
| External access | `llm`, `connector_slack`, `connector_teams`, `connector_github`, `connector_jira`, `connector_outlook` | The LLM provider abstraction and one adapter per data source |
| Analysis | `agent`, `embedder`, `signatures`, `noise_filter` | Prompt construction and LLM calls, project classification, signature parsing, pre-LLM filtering |
| Interpretation | `correlator`, `situation_manager`, `attention`, `contacts` | Cross-source situation grouping and lifecycle, learned ranking, the people directory |
| Orchestration | `orchestrator`, `seeder` | The scan / reanalyze / ingest loops and the first-run bootstrap state machine |
| Composition root | `app` | Wires all of the above together and exposes the HTTP surface |

## Packages
<!-- BEGIN: AUTO-GENERATED PACKAGE INDEX -->
| Package | Role | README |
|---|---|---|
| `api` | Parsival API service: FastAPI app, connectors, LLM analysis, and SQLite storage. | [../api/README.md](../api/README.md) |
<!-- END: AUTO-GENERATED PACKAGE INDEX -->

## Module-level entry points

The service itself is started by uvicorn as `app:app` (see the Dockerfile —
modules are copied flat into `/app`, so they are imported as top-level
modules, not as `api.*`). Everything else that is directly executable lives
in `scripts/` and is **not** part of the `api` package:

| Script | Runs where | What it does |
|---|---|---|
| [`outlook_sidecar.py`](../scripts/outlook_sidecar.py) | Windows host | Reads Outlook via `win32com` and POSTs items to `/ingest`. Needs local mail-client state, so it cannot run in the container. |
| [`thunderbird_sidecar.py`](../scripts/thunderbird_sidecar.py) | Ubuntu host | Same contract, reading a local mbox/Maildir. |
| [`seed_test_data.py`](../scripts/seed_test_data.py) | Anywhere | Populates a database with synthetic items for development. |
| [`migrate_to_sqlite.py`](../scripts/migrate_to_sqlite.py) | Once | One-time migration from the legacy TinyDB `page.db`. |
| [`gen_code_map.py`](../scripts/gen_code_map.py) | Dev + CI | Regenerates this file and `api/README.md` from docstrings. Run with `--check` it fails on drift. |
| [`backup_db.sh`](../scripts/backup_db.sh) | Host cron | Online SQLite backup, safe against a running WAL database. |

## Regenerating this page

The package table above and the Public API section of `api/README.md` are
generated from docstrings — edit the docstrings, not the generated blocks:

```bash
python scripts/gen_code_map.py --src-root api --flat           # regenerate
python scripts/gen_code_map.py --src-root api --flat --check   # CI: fail on drift
```

Everything outside the `AUTO-GENERATED` markers, including this section, is
hand-written and preserved across regeneration.
