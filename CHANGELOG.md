# Changelog

Notable changes to Parsival — features added, behavior changes, and fixes
(`STANDARDS.md` DOC-009). Newest first.

Entries are grouped by the date the work landed rather than by version:
the repository carries no release tags yet (the single existing tag,
`premerge-43-backup`, is a backup marker, not a milestone). Once VCS-003
milestone tags exist, sections become versions.

Everything before `[Unreleased]` was reconstructed from git history when this
file was introduced, summarised at feature granularity. From here on, entries
are written in the same change set as the change they describe.

## [Unreleased]

Day-0 engineering standards adoption (`chore/gatehouse-standards`). No
runtime behavior changes — the service is untouched; this is tooling,
documentation, and CI.

### Added
- **Code map generated from docstrings** (DOC-006/007). `docs/code_map.md`
  indexes the package and carries a hand-written layer map of all 23 `api/`
  modules; `api/README.md` — which did not previously exist, leaving DOC-006
  half-met — gains a narrative header plus a generated Public API section.
  `scripts/gen_code_map.py` is vendored from gatehouse; it parses with `ast`
  and never imports the code it documents.
- **`--flat` mode for the generator.** Upstream discovers packages as the
  subdirectories of `--src-root` containing `__init__.py`; parsival is a
  single flat package, so that found nothing and produced an empty map. Four
  fixture tests cover the new mode, including one asserting the flag is
  load-bearing.
- **Code-map enforcement** (DOC-008): a pre-commit hook that regenerates on
  any change to `api/*.py`, and a CI step running `--check` so documentation
  drift fails the build.
- **Docstrings for 44 previously undocumented look-ahead functions** (DOC-002)
  — 26 routes in `api/app.py` and 18 helpers in `api/db.py`.
- **Pinned lint/type toolchain** (CODE-003/004): `pyproject.toml` declares
  PEP 8 and PEP 257, pinning ruff 0.15.14 and mypy 2.1.0 exactly.
  `pre-commit` 4.6.1 pinned alongside them.

### Changed
- **mypy now actually runs in CI.** The workflow previously ran pytest and
  nothing else, so the type gate existed only on paper —
  `pyproject.toml`'s ratchet comment claimed a regression "fails CI" when it
  could not. `api/` is enforced (23 files clean); eight modules are listed as
  named, shrinking debt. `scripts/` stays out for now with 7 pre-existing
  errors.
- CI gates run after the tests under `!cancelled()`, so one failing gate never
  masks the others or the test results.
- 507 ruff safe autofixes applied across the tree.

### Fixed
- **`.gitattributes` was forcing LF onto Windows scripts** (CTR-001). The
  blanket `eol=lf` rule caught the new `scripts/run_outlook_sidecar.cmd`;
  `*.cmd`, `*.bat`, and `*.ps1` are now `eol=crlf`. Binary attributes
  completed for `woff2`, which `git check-attr` reported as unspecified
  (CTR-003).
- **Main's CI was red from a stale test.** The resilient-ingest feature made a
  single persistent 500 skippable, but a test still required `SystemExit`.
  Replaced with two tests matching the documented contract, including first
  coverage of the systemic-abort path.

### Known gaps
- `ruff check` is **not** yet wired into CI, though CODE-003 requires it: 99
  findings remain (82 in `api/`, 15 in `tests/`, 2 in `scripts/`) and enabling
  the gate today would land main red. It goes in with the lint debt and the
  formatting pass.
- Six module docstrings and a number of comments still say "Squire" or
  reference `squire#NN` issues, left over from the rename (DOC-004).

## 2026-07 — Sidecar resilience

### Added
- **Outlook sidecar high-water-mark tracking and resilient ingest.** A batch
  that fails is retried and then bisected to isolate a poison item, which is
  skipped rather than aborting the run, so one bad email cannot stall
  ingestion. Drops beyond `_MAX_SKIPPED_ITEMS` are treated as systemic: the
  run aborts and the high-water mark is *not* advanced, so the whole window is
  retried next run instead of being silently written off.

### Fixed
- Forced LF line endings on exec-critical files to stop a Windows checkout
  corrupting a shebang and killing the container ENTRYPOINT with exit 255.

## 2026-05 — Card parity follow-ups

### Added
- Engineering knowledge graph (EKG) concept stub and a written rationale for
  the graph approach.

### Fixed
- The synthesized `manual_<doc_id>` items row is dropped when its todo is
  deleted, instead of lingering as an orphan (#94).

## 2026-04 — Look-ahead board, attention model, and the Parsival rename

The project's largest period of change: 140 commits.

### Added
- **Look-ahead board** (#48) — a two-week planning view with a 14-day
  Sun–Sat grid, drag-to-reschedule, per-project shift schedules, a typed
  global resource catalog (BOM) with `needed`/`secured`/`consumed` status, and
  card dependencies.
- **Look-ahead templates** (#49) — repeatable task graphs with relative
  offsets, dependencies, and resource requirements; instantiate, reschedule,
  detach, auto-complete, and opt-in version upgrade for existing instances.
- **Cross-system linking** (#50) — procedure-doc URLs, item links into the
  analyses table, and an LLM annotator proposing related items. Acceptance is
  always user-confirmed; rejections are remembered so the annotator does not
  re-propose them.
- **Cards ↔ todos round-trip** (#71) — every look-ahead card is mirrored by a
  linked action item; completing either side flips the other.
- **Per-task `work_days` mask** (#73), so the workweek is per-task rather than
  per-template.
- **Adaptive attention model** (B7) — a learned ranking built from observed
  behavior (opened, tagged, noised, dismissed) using incrementally maintained
  attended/ignored centroids, replacing the fixed priority hierarchy.
- **Situation lifecycle workflow** (B5) — `new` / `investigating` / `waiting`
  / `resolved` / `dismissed` with transition history, follow-up dates, a
  stale-decay advisory flag, and manual split/merge (#40).
- **Pre-scan noise filters** (B6), evaluated before the LLM so
  known-irrelevant items never cost an inference cycle.
- **Scheduled auto-scans** (B1) with per-connector intervals.
- **Contacts directory** (#24) built from email headers, keyed by a stable
  serial id rather than an email address, plus a **signature parser** (#31)
  that enriches contacts from message bodies and never overwrites
  hand-edited fields.
- **Passdown HTML email generation** (#39) and a **priority override feedback
  loop** (#38) that prompt-injects recent corrections at inference time.
- **Configurable analysis provider** — local Ollama, Ollama Cloud, or the
  Claude API, behind the `llm.py` abstraction.
- **Background batch processing via merLLM**, with re-analysis submitted to
  the background priority bucket and polled for results.
- **Mobile UX pass** (#41) — Right Now panel, single-column item cards, and
  swipe gestures.
- Deep analysis from situation cards (#1), batch status proxy, and GitHub
  Actions CI.

### Changed
- **Renamed Squire → Parsival** across branding, service names, and the
  database path; the domain moved to `parsival.hexcaliper.com`.
- **merLLM owns GPU concurrency** (#33) — the parsival-side throttle was
  removed, and every LLM call routes to the appropriate priority bucket (#34).
- Re-analysis is durable-only and aborts when merLLM is unavailable rather
  than silently falling back to a synchronous proxy (#47).
- Re-analysis processes items in hierarchy order (`user` → `project` →
  `topic` → `general`), newest first within each tier.

### Fixed
- **OAuth tokens are encrypted at rest** (#5), OAuth flows gained CSRF `state`
  validation and configurable redirect URIs (#4), and GitHub notification
  fetching follows pagination instead of silently truncating (#6).
- Batch job polling: wrong URL, wrong response field, 409 handling, and
  startup resume (#3).
- `db.lock` made re-entrant (`RLock`) so nested locked helpers cannot
  self-deadlock; pytest-timeout added; the Dockerfile switched to a glob so a
  new module can no longer be silently omitted from the image (#32).
- Thread-aware analysis via a prior-message todos hint (#79, #82) and wider
  todo dedup across Outlook reply chains (#77).
- Recipient-scope-aware action item extraction, restoring delegated-work
  tracking (#25).
- Numerous look-ahead board fixes: browser-portable drag-and-drop, overlapping
  cards packed into sub-rows, and BOM status persisting immediately.

## 2026-03 — Initial service, situation layer, and the SQLite migration

### Added
- Initial FastAPI companion service with a pytest suite, Slack and Outlook
  ingestion, and a host sidecar authenticating via a Cloudflare Access service
  token with credentials held in Windows Credential Manager.
- **Situation layer** — cross-source correlation, scoring, and LLM synthesis
  of related items.
- **Knowledge graph (GraphRAG)** — items indexed into typed nodes and
  weighted edges, with related items retrieved into the analysis prompt.
- **Microsoft Teams connector** with an OAuth flow.
- **Seed workflow** for bootstrapping project intelligence from existing data,
  converted into a state machine with an automated ingest watch and run as a
  background job to avoid nginx 504s.
- Context hierarchy (`user`/`project`/`topic`/`general`), project and noise
  keyword learning, sender signals, and passdown detection.
- Embedding-based project classification with subdivision centroids.
- Item detail panel, full-text search with date/hierarchy/project filters,
  scan cancel, reply detection, and a mobile-responsive layout.
- Project briefing generation, per-category keyword learning, and assignee
  tracking.
- GPU meter with multi-GPU support, plus a CPU/RAM system meter.
- `DESIGN.md` and `INSTRUCTIONS.md`.

### Changed
- **Migrated storage from TinyDB to SQLite** (WAL), alongside the knowledge
  graph tables.
- **Extracted `situation_manager.py`, `seeder.py`, and `orchestrator.py` out
  of `app.py`**, guarded by unit tests written before the refactor.
- Overhauled the category schema to the current four categories.
- nginx bound to loopback only, reached via the Cloudflare tunnel, with
  security headers added.

### Fixed
- `correlator.py` was missing from the Docker image, causing silent save
  failures.
- nginx 403 on SPA routes; detail-panel scrolling; several search-bar
  credential-autofill and chip-CSS collisions.
- Ollama calls throttled with a semaphore to prevent GPU overload (later
  superseded by merLLM owning concurrency).
