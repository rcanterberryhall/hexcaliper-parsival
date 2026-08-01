# api

> The entire Parsival backend: one FastAPI service that pulls items from five
> connectors, analyses them with an LLM, and stores the results in SQLite.

## Purpose

`api/` is the whole server side of Parsival — roughly 14,700 lines across 23
flat modules, with no subpackages. Everything the `page-api` container runs
lives here; `scripts/`, `tests/`, and `web/` are its only in-repo neighbours.

The main data path runs left to right: a **connector** (`connector_slack`,
`connector_teams`, `connector_github`, `connector_jira`, `connector_outlook`)
or a host sidecar posting to `/ingest` produces raw items → **`orchestrator`**
drives the scan/reanalyze/ingest loops and applies `noise_filter` before
spending an inference → **`agent`** builds the prompt (enriched with
`graph` GraphRAG context and `embedder` project classification) and calls
**`llm`**, which abstracts local Ollama, Ollama Cloud, and the Claude API →
results are written through **`db`** to a single SQLite WAL file. On top of
that sit the interpretation layers: `correlator` and `situation_manager` group
items into situations, `attention` learns a ranking from user behaviour, and
`contacts` + `signatures` maintain the people directory. `app` exposes all of
it as 107 HTTP routes.

This file is the **code-structure** orientation. The repository
[`README.md`](../README.md) is the feature, configuration, and API-endpoint
reference for operators and users — it documents *what Parsival does*; this
one documents *how the source is arranged*.

The "Public API" section below is generated from docstrings by
`scripts/gen_code_map.py` and is not hand-edited.

## Key concepts

- **Composition root.** `app.py` is where everything is wired together: it
  imports 15 of the other 22 modules and owns all 107 routes, request
  logging middleware, and startup hooks. New endpoints go here.
- **Foundation layer.** `config` (imported by 13 modules) holds hot-reloadable
  runtime settings; `models` (imported by 9) holds the shared item/analysis
  dataclasses. Nearly everything depends on one or both.
- **One database, one lock.** `db` owns a single SQLite WAL connection plus a
  module-level `threading.RLock` exported as `db.lock`. Callers wrap mutations
  in `with db.lock:`.
- **Provider indirection.** Every LLM call — analysis, seeding, correlation,
  briefing — goes through `llm`, so switching between local Ollama, Ollama
  Cloud, and Claude is a settings change. Bulk re-analysis is submitted to
  merLLM's background batch queue rather than called inline.
- **Deterministic before probabilistic.** `noise_filter`, passdown detection,
  and learned-sender matching all run *before* the LLM, so known-irrelevant or
  already-classifiable items never cost an inference cycle.
- **Flat module namespace.** Modules import each other as top-level modules
  (`import db`), not as package members — see the first pitfall below.

## How it relates to other packages

`api` is the repository's only source package, so its relationships are with
non-package neighbours and external services rather than sibling packages:

- **`scripts/`** — host-side sidecars (`outlook_sidecar.py`,
  `thunderbird_sidecar.py`) that run outside Docker because they need local
  mail-client state, and feed the API by POSTing to `/ingest`. They depend on
  the HTTP contract only, never by import. `scripts/gen_code_map.py`
  regenerates this file's Public API section.
- **`tests/`** — imports these modules directly after `conftest.py` puts
  `api/` on `sys.path`.
- **`web/`** — the vanilla-JS UI, served by nginx and volume-mounted, which
  talks to this package only over `/page/api/*`.
- **`data/`** — the SQLite WAL database file this package reads and writes.
- **External** — merLLM/Ollama (or the Claude API) for inference, and the
  Slack, Teams, GitHub, and Jira REST APIs for connector data.

## Common pitfalls

- **These modules are imported flat, not as a package.** Despite
  `api/__init__.py` existing, nothing imports `api.db` — the Dockerfile does
  `COPY *.py .` into `/app` and runs `uvicorn app:app`, so at runtime every
  module sits at the top level; `tests/conftest.py` reproduces that by
  inserting `api/` onto `sys.path`. Relative imports (`from .db import ...`)
  and package-qualified imports (`from api import db`) both break in the
  container. Keep using `import db`.
- **`config` and `crypto` are mutually dependent.** `crypto` imports `config`
  for the encryption key, and `config` imports `crypto` to decrypt stored
  tokens. The cycle is broken by *function-local* imports inside `config`,
  each marked with a comment. Hoisting them to the top of the file is an
  import-time crash, not a style improvement.
- **`db.lock` is re-entrant on purpose.** It is an `RLock` so that a locked
  helper may call another locked helper without self-deadlocking. Swapping it
  for a plain `Lock` will wedge the service under nested calls.
- **Docstrings are load-bearing.** The Public API section below and
  `docs/code_map.md` are generated from them, and CI runs the generator in
  `--check` mode. Editing a docstring without regenerating fails the build —
  run `python scripts/gen_code_map.py --src-root api --flat`.
- **Some docstrings still say "Squire."** `config`, `db`, `graph`, `models`,
  `orchestrator`, and `signatures` carry the project's former name, and a
  number of comments reference `squire#NN` issues. These are stale, not a
  second component.

## Public API
<!-- BEGIN: AUTO-GENERATED PUBLIC API (do not edit) -->
### `api.agent`
*agent.py — LLM-powered analysis pipeline.*

- `generate_project_briefing(project_name: str, intel_facts: list[str], situations: list[str], action_items: list[str]) -> str` — Ask the LLM to write a 2-3 sentence status paragraph for a project.
  <details><summary>full docstring</summary>

  :param project_name: Name of the project (or ``"General"`` for untagged).
  :param intel_facts:  Recent intel fact strings for this project.
  :param situations:   Active situation title + status strings.
  :param action_items: Open action item description strings.
  :return: Prose status paragraph, or empty string on failure.
  :rtype: str
  </details>
- `extract_keywords(project_name: str, title: str, body: str) -> list[str]` — Ask the LLM to extract keywords from an item for project context learning.
  <details><summary>full docstring</summary>

  :param project_name: Name of the project being trained.
  :param title: Item title.
  :param body: Item body text (will be truncated to 2000 chars).
  :return: List of keyword strings, empty list on failure.
  </details>
- `extract_emails(text: str) -> list[str]` — Extract unique, lowercase email addresses from a free-form string.
  <details><summary>full docstring</summary>

  Covers RFC-style headers such as ``"Name <addr@host.com>"`` as well as
  bare addresses and semicolon/comma-separated lists.

  :param text: Any string that may contain email addresses.
  :type text: str
  :return: Deduplicated list of lowercase email addresses.
  :rtype: list[str]
  </details>
- `compute_recipient_scope(user_email: str, to_field: str, cc_field: str) -> dict` — Classify how broadly an email is addressed, from the user's perspective.
  <details><summary>full docstring</summary>

  Returns a dict with:
    - ``scope``: one of ``"direct"``, ``"small"``, ``"group"``, ``"broadcast"``
    - ``to_count`` / ``cc_count`` / ``total``: unique visible address counts
    - ``dls``: list of distribution-list addresses found in To/CC
    - ``user_in_to`` / ``user_in_cc``: whether the user is a visible recipient

  Tiers:
    - ``direct``    — exactly 1 visible address (To) and it is the user
    - ``small``     — 2–4 visible addresses total
    - ``group``     — 5–10 visible addresses total
    - ``broadcast`` — 11+ addresses, any distribution list detected, or the
                      user is not a visible recipient (received via list/BCC)

  Sources without populated headers (Jira, GitHub, Slack DMs) get
  ``total=0`` and ``scope="direct"`` so the downstream rules treat them as
  already-targeted.

  :param user_email: Configured user email (may be empty).
  :param to_field:   Raw ``To`` header value.
  :param cc_field:   Raw ``CC`` header value.
  :return: Scope classification dict.
  :rtype: dict
  </details>
- `resolve_owner_email(owner: str, *header_fields: str) -> str | None` — Try to resolve a person's name to an email address.
  <details><summary>full docstring</summary>

  First scans the supplied To/CC header fields for ``"Display Name <email>"``
  pairs whose display name contains ``owner`` as a case-insensitive
  substring.  When that fails, falls back to the master contacts table —
  important for delegated directives like "Mike, pull the drawings" in an
  email where Mike isn't one of the visible recipients.

  :param owner: Person name returned by the LLM (e.g. ``"John Johnson"``).
  :param header_fields: One or more raw To/CC header strings.
  :return: Matched email address (lowercase), or ``None`` if no match found.
  :rtype: str or None
  </details>
- `build_prompt(item: RawItem, *, thread_todos: list[dict] | None=None) -> str` — Build the LLM analysis prompt for an item without submitting it.
  <details><summary>full docstring</summary>

  Returns the fully formatted prompt string that ``analyze`` would send to
  Ollama.  Used by the batch submission path in ``orchestrator.py`` to
  construct the prompt when routing through merLLM's batch API.

  :param item: The raw item to build a prompt for.
  :type item: RawItem
  :param thread_todos: Open todos already saved for strictly-earlier items
      in the same ``conversation_id``, rendered as a "do not re-emit" hint
      to suppress paraphrased duplicates across reply chains (parsival#79).
  :return: Fully formatted prompt string.
  :rtype: str
  </details>
- `build_analysis_from_llm_json(item: RawItem, llm_json_text: str, *, scope_info: dict) -> Analysis` — Parse an LLM JSON response and build a fully populated ``Analysis``.
  <details><summary>full docstring</summary>

  Shared by :func:`analyze` (sync path) and the batch poll path in
  :mod:`orchestrator` so the two cannot drift.  Applies every deterministic
  override the sync path applies:

  - ``_detect_quarantine_noise`` (quarantine digests → noise)
  - ``fyi`` / ``noise`` clears any action items the LLM returned
  - Jira fallback (open tickets always get a "Work on: …" action item)
  - ``_detect_passdown`` (deterministic shift handoff detection)
  - ``_validated_project_tags`` (drops invented project names)
  - Both ``project_tags`` (plural) and ``project_tag`` (singular) keys

  The caller is responsible for computing ``scope_info`` from the item's
  To/CC fields and (in the sync path) for surfacing it as a prompt hint.

  Reads from ``item.metadata``:
      ``to``, ``cc``, ``is_replied``, ``replied_at``, ``hierarchy``,
      ``direction``, ``conversation_id``, ``conversation_topic``,
      ``project_tag``, ``due``.

  :param item: The raw item that was analysed.
  :param llm_json_text: Raw LLM response text (will be tolerantly parsed).
  :param scope_info: Result of :func:`compute_recipient_scope`.
  :return: A populated ``Analysis`` instance.
  </details>
- `analyze(item: RawItem, *, priority: str='short', thread_todos: list[dict] | None=None) -> Analysis` — Send a single item to Ollama and parse the structured JSON response.
  <details><summary>full docstring</summary>

  The prompt is built with full user context (name, email, projects, topics,
  noise keywords) and includes the ``to``/``cc`` fields from item metadata so
  the model can apply recipient-based hierarchy rules.

  If the LLM returns malformed JSON, all fields default to safe fallback
  values so the item is still persisted rather than silently dropped.
  Jira items without action items receive an automatic fallback action so
  open tickets are always surfaced.

  ``is_passdown`` is forced to ``True`` when ``_detect_passdown`` matches —
  the only hard deterministic override.  All other classification fields
  (``hierarchy``, ``project_tag``, ``category``, etc.) come from the LLM.
  Sender/group address matches from ``_match_sender`` are passed into the
  prompt as a hint so the model can weigh them against the actual content.
  ``hierarchy`` and ``project_tag`` fall back to values pre-set in
  ``item.metadata`` (e.g. by the Slack connector) when the LLM omits them.

  :param item: The raw item to analyse.
  :type item: RawItem
  :param priority: merLLM priority bucket. Defaults to ``short`` for the
      per-item ingest path; re-analyze passes ``background`` so bulk
      re-runs cannot starve chat or regular ingest traffic.
  :return: Structured analysis result with all enrichment fields populated.
  :rtype: Analysis
  :raises requests.HTTPError: If the Ollama API request fails.
  </details>
- `analyze_batch(items: list[RawItem], progress_cb=None) -> list[Analysis]` — Analyse a list of items sequentially, with optional progress reporting.
  <details><summary>full docstring</summary>

  Failed items are logged and skipped rather than aborting the batch, so a
  single Ollama timeout does not prevent the remaining items from being
  processed.

  :param items: List of raw items to analyse.
  :type items: list[RawItem]
  :param progress_cb: Optional callback invoked after each item with the
                      signature ``(index, total, source, title)``.
  :type progress_cb: callable, optional
  :return: List of analysis results for all successfully processed items.
  :rtype: list[Analysis]
  </details>

### `api.app`
*app.py — Parsival FastAPI application.*

- `async request_logging(request: Request, call_next)` — Log every HTTP request with method, path, status, duration, and user.
- `get_user(request: Request) -> str` — Extract the authenticated user's email from the Cloudflare Access header.
  <details><summary>full docstring</summary>

  Mirrors hexcaliper's user-scoping convention.  Falls back to
  ``"local@dev"`` for requests that bypass Cloudflare Access (e.g. local
  development without the tunnel).

  :param request: The incoming FastAPI request.
  :type request: Request
  :return: Authenticated user email, or ``"local@dev"`` if not present.
  :rtype: str
  </details>
- `now_iso() -> str` — Return the current UTC time as an ISO 8601 string.
  <details><summary>full docstring</summary>

  :return: Current UTC timestamp in ISO 8601 format.
  :rtype: str
  </details>
- `scan_state: dict = {'running': False, 'cancelled': False, 'progress': 0, 'total': 0, 'current_source': '', 'current_item': '', 'message': 'idle', 'ingest_pending': 0, 'situations_pending': 0, 'total_items': 0, 'completed_items': 0, 'estimated_minutes_remaining': 0}` — (undocumented)
- `gpu_stats()` — Return live GPU utilisation, VRAM usage, and temperature for every GPU.
  <details><summary>full docstring</summary>

  Read via NVML across all detected devices.

  Used by the frontend GPU meter widgets.  Returns ``{"ok": False}`` when
  ``pynvml`` is not installed or no NVIDIA device is present — the UI will
  fade the meters gracefully in that case.

  :return: Dict with ``ok``, and when successful: ``gpus`` — a list of
      per-device dicts each containing ``name``, ``gpu_util`` (int %),
      ``mem_used`` (bytes), ``mem_total`` (bytes), ``temperature`` (°C).
  :rtype: dict
  </details>
- `system_stats()` — Return live CPU utilisation and RAM usage via psutil.
  <details><summary>full docstring</summary>

  Always available (no optional dependency).  Used by the frontend
  system meter widgets alongside the GPU meters.

  :return: Dict with ``ok``, ``cpu_util`` (int %), ``mem_used`` (bytes),
      ``mem_total`` (bytes).
  :rtype: dict
  </details>
- `health()` — Service health check.
  <details><summary>full docstring</summary>

  Mirrors hexcaliper's ``/health`` response shape.  Returns ``{"ok": True}``
  plus any configuration warnings from ``config.validate()``.

  :return: Dict with ``ok`` (bool) and ``warnings`` (list of strings).
  :rtype: dict
  </details>
- `reset_db()` — Truncate all data tables while preserving saved settings.
  <details><summary>full docstring</summary>

  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `get_senders()` — Return a flat sorted list of all known sender email addresses across all projects.
  <details><summary>full docstring</summary>

  Combines static ``senders`` and runtime-learned ``learned_senders`` from
  every configured project, deduplicates, and returns them sorted.  Used by
  the frontend assign-picker to offer autocomplete suggestions.

  :return: ``{"senders": [...]}`` — sorted list of unique lowercase addresses.
  :rtype: dict
  </details>
- `get_projects()` — Return all configured projects with learning metadata.
  <details><summary>full docstring</summary>

  For each project in ``config.PROJECTS``, returns:
  - ``name``, ``keywords``, ``channels`` — static config fields.
  - ``learned_keywords``, ``learned_count`` — keywords grown at runtime via
    the tagging workflow.
  - ``learned_senders``, ``sender_count`` — email addresses grown via tagging.
  - ``embedding_items``, ``embedding_subs`` — embedding centroid stats from
    the ``embedder`` module.

  :return: List of project dicts with learning metadata.
  :rtype: list[dict]
  </details>
- `TagRequest` — Request body for ``POST /analyses/{item_id}/tag``.
  <details><summary>full docstring</summary>

  :ivar project: Exact name of the target project (must match a configured project).
  </details>
  - `project: str` — (undocumented)
- `get_analysis(item_id: str)` — Return a single deserialized analysis record with attention score attached.
  <details><summary>full docstring</summary>

  Used by the frontend detail-panel cold path (``openTodoDetail``) so that
  opening an item that isn't yet in the in-memory ``allAnalyses`` cache
  doesn't have to refetch the entire ``/analyses`` list just to look up one
  row.

  :param item_id: Stable ID of the analysis item.
  :return: Deserialized analysis dict with ``attention_score`` field.
  :raises HTTPException 404: If no item with ``item_id`` exists.
  </details>
- `patch_analysis(item_id: str, body: dict, background_tasks: BackgroundTasks)` — Update editable fields on a stored analysis record.
  <details><summary>full docstring</summary>

  Accepts any subset of ``priority``, ``category``, ``project_tag``, and
  ``is_passdown``.  Only values that pass the allowed-value guard are
  applied; unknown or invalid values are silently ignored.

  Side effects:
  - Setting ``category="noise"`` also clears ``has_action`` and removes all
    associated todos.
  - Changing ``priority`` syncs the new value to all associated todo rows.
  - Changing ``project_tag`` or ``category`` triggers a background embedding
    update.

  :param item_id: Stable ID of the analysis item to update.
  :param body: Partial update dict; accepted keys: ``priority``, ``category``,
               ``project_tag``, ``is_passdown``.
               Content-level fields (issue #85): ``title``, ``summary``,
               ``user_summary``, ``urgency_reason``/``urgency``,
               ``body_preview``, ``hierarchy``, ``goals``, ``key_dates``.
  :return: ``{"ok": True}`` plus all fields that were actually updated.
  :raises HTTPException 400: If no valid fields are present in ``body``.
  :raises HTTPException 404: If no item with ``item_id`` exists.
  </details>
- `tag_item(item_id: str, body: TagRequest, background_tasks: BackgroundTasks)` — Tag an analysis item to a project and trigger background keyword/sender learning.
  <details><summary>full docstring</summary>

  Sets the item's ``project_tag`` synchronously, then runs a background task
  (``learn``) that:

  1. Calls ``extract_keywords`` to get 5–10 characteristic keywords from the
     item's body/summary, then merges them into the project's
     ``learned_keywords`` list (capped at 100 entries).
  2. Extracts all email addresses from ``author``, ``to_field``, and
     ``cc_field``, strips the user's own address, and merges the remainder
     into the project's ``learned_senders`` list (capped at 50 entries).
  3. Persists the updated project config back to settings and calls
     ``config.apply_overrides`` so future analyses benefit immediately.
  4. Calls ``embedder.update_project`` to add/update the item's vector in
     the project's embedding centroid.

  :param item_id: Stable ID of the analysis item to tag.
  :param body: Must contain a ``project`` field matching a configured project name.
  :return: ``{"ok": True, "project": project_name}``
  :raises HTTPException 404: If the item or project does not exist.
  </details>
- `mark_noise(item_id: str, background_tasks: BackgroundTasks)` — Mark an analysis item as irrelevant and grow the noise keyword filter.
  <details><summary>full docstring</summary>

  Sets ``category="noise"``, ``priority="low"``, and ``has_action=False``
  synchronously, and removes all associated todos.  Then runs a background
  task (``_learn_noise_from_record``) that extracts keywords from the item
  and merges them into ``config.NOISE_KEYWORDS`` (capped at 200).

  :param item_id: Stable ID of the analysis item to mark as noise.
  :return: ``{"ok": True}``
  :raises HTTPException 404: If no item with ``item_id`` exists.
  </details>
- `record_item_action(item_id: str, body: dict)` — Record a user interaction for attention model training.
  <details><summary>full docstring</summary>

  :param body: ``{"action_type": "opened"}``  (or tagged, noised, etc.)
  :return: ``{"ok": True}``
  </details>
- `attention_summary()` — Return the attention model summary for the merLLM 'My Day' panel.
  <details><summary>full docstring</summary>

  Includes cold-start flag, centroid counts, active situation counts,
  and overdue follow-up count.
  </details>
- `get_settings()` — Return all current configuration values for the settings UI.
  <details><summary>full docstring</summary>

  Credential fields are partially masked via ``_mask`` so the frontend can
  distinguish "set" from "not set" without exposing full secrets.

  :return: Dict of all current config values, with sensitive fields masked.
  :rtype: dict
  </details>
- `save_settings(body: dict)` — Persist settings to SQLite and hot-reload config.
  <details><summary>full docstring</summary>

  Merges ``body`` into the existing settings record.  Any field whose value
  is a string containing ``•`` (the mask character) is skipped — this
  prevents the frontend from accidentally overwriting a real credential with
  a masked placeholder.

  When the ``projects`` list changes, analyses tagged to removed projects
  have their ``project_tag`` cleared so no orphan tags remain in the DB.

  :param body: Partial or full settings dict.  Unknown keys are stored as-is.
  :return: ``{"ok": True, "warnings": [...]}``
  :rtype: dict
  </details>
- `get_noise_filters()` — Return the current list of noise filter rules.
- `add_noise_filter(body: dict)` — Append a noise filter rule.
  <details><summary>full docstring</summary>

  :param body: ``{"type": "sender_contains", "value": "noreply@"}``
  :return: Updated filter list.
  :raises HTTPException 422: If the rule is invalid.
  </details>
- `delete_noise_filter(index: int)` — Remove a noise filter rule by its zero-based index.
  <details><summary>full docstring</summary>

  :param index: Zero-based index of the rule to remove.
  :return: Updated filter list.
  :raises HTTPException 404: If index is out of range.
  </details>
- `count_filtered_items()` — Return the number of items stored with category='filtered'.
- `IngestRequest` — Request body for ``POST /ingest``.
  <details><summary>full docstring</summary>

  :ivar items: List of raw item dicts.  Each dict must have an ``item_id``
               key; all other fields correspond to ``RawItem`` fields.
  </details>
  - `items: list[dict]` — (undocumented)
- `ingest(body: IngestRequest, background_tasks: BackgroundTasks)` — Receive raw items from host sidecar scripts (Outlook, Thunderbird, etc.).
  <details><summary>full docstring</summary>

  Deduplicates by ``item_id`` against the items table — items that have
  already been processed are silently skipped.  New items are queued as a
  background task so the HTTP response is returned immediately.

  :param body: List of raw item dicts.
  :return: ``{"received": N, "skipped": M}``
  :rtype: dict
  </details>
- `ScanRequest` — Request body for ``POST /scan``.
  <details><summary>full docstring</summary>

  :ivar sources: Connector names to fetch from.  Defaults to all four
                 standard connectors.
  </details>
  - `sources: list[str] = ['slack', 'github', 'jira', 'outlook']` — (undocumented)
- `start_scan(body: ScanRequest)` — Start a multi-source scan in the background.
  <details><summary>full docstring</summary>

  Returns immediately; poll ``GET /scan/status`` for progress.

  :param body: Scan request specifying which sources to include.
  :return: ``{"status": "started", "sources": [...]}``
  :raises HTTPException 409: If a scan or re-analysis is already running.
  </details>
- `scan_status()` — Return the current scan/ingest/reanalyze progress state.
  <details><summary>full docstring</summary>

  :return: Current ``scan_state`` dict plus ``auto_scans`` schedule status.
  :rtype: dict
  </details>
- `cancel_scan()` — Signal a running scan to stop after the current item finishes.
  <details><summary>full docstring</summary>

  :return: ``{"ok": True}`` if a scan was running, else ``{"ok": False, ...}``.
  :rtype: dict
  </details>
- `stop_all_analysis()` — Gracefully halt all ongoing analysis activity.
  <details><summary>full docstring</summary>

  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `start_reanalyze()` — Re-run LLM analysis on all stored items using the current config.
  <details><summary>full docstring</summary>

  Returns immediately; poll ``GET /scan/status`` for progress.

  :return: ``{"status": "started", "item_count": N}``
  :raises HTTPException 409: If a scan or re-analysis is already running.
  </details>
- `reanalyze_count()` — Return the number of stored items that would be processed by ``POST /reanalyze``.
  <details><summary>full docstring</summary>

  :return: ``{"count": N}``
  :rtype: dict
  </details>
- `get_todos(source: str | None=None, priority: str | None=None, done: bool=False)` — Return action-item todos, optionally filtered and sorted by priority.
  <details><summary>full docstring</summary>

  By default only open (``done=False``) items are returned.  Results are
  sorted by priority (high → medium → low) then by creation time ascending.
  A ``doc_id`` field is added to every returned row for use in PATCH/DELETE.

  :param source: Filter to items from a specific connector.
  :param priority: Filter to items with a specific priority level.
  :param done: If ``True``, include completed items.
  :return: List of todo dicts sorted by priority then creation time.
  :rtype: list[dict]
  </details>
- `create_todo(body: dict)` — Create a manual action item.
  <details><summary>full docstring</summary>

  Manual todos are not tied to LLM analysis — they represent work the user
  wants to track themselves.  The ``item_id`` field is optional; when
  supplied the todo is associated with an existing analysis item.

  :param body: Dict with required ``description`` and optional ``deadline``,
               ``priority``, ``project_tag``, ``item_id``.
  :return: ``{"ok": True, "doc_id": <id>}``
  :raises HTTPException 400: If ``description`` is missing or empty.
  </details>
- `get_todos_assigned_count()` — Return a count of open todos in the 'assigned' state.
  <details><summary>full docstring</summary>

  Counts only rows with a non-empty ``assigned_to``. Backs the Assigned vtab
  badge so the UI doesn't have to fetch and client-side filter the full
  open-todo set on every mutation.

  :return: ``{"count": N}``
  :rtype: dict
  </details>
- `patch_todo(doc_id: int, body: dict)` — Update a todo item.
  <details><summary>full docstring</summary>

  Accepted fields: ``status``, ``done``, ``assigned_to``, ``description``,
  ``deadline``, ``priority``, ``project_tag``.

  :param doc_id: Integer id of the todo record.
  :param body: Partial update dict.
  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `delete_todo(doc_id: int)` — Permanently delete a todo item by its integer id.
  <details><summary>full docstring</summary>

  :param doc_id: Integer id of the todo record to remove.
  :return: HTTP 204 No Content.
  </details>
- `get_intel(source: str | None=None, project: str | None=None, include_dismissed: bool=False)` — Return intel (information) items sorted by timestamp descending.
  <details><summary>full docstring</summary>

  A ``doc_id`` field is added to each returned row.

  :param source: Filter to items from a specific connector.
  :param project: Filter to items tagged to a specific project.
  :param include_dismissed: When ``True``, dismissed items are included.
  :return: List of intel dicts sorted newest-first.
  :rtype: list[dict]
  </details>
- `delete_intel(doc_id: int)` — Permanently delete an intel item by its integer id.
  <details><summary>full docstring</summary>

  :param doc_id: Integer id of the intel record to remove.
  :return: HTTP 204 No Content.
  </details>
- `patch_intel(doc_id: int, body: dict)` — Update an intel item, currently limited to toggling the ``dismissed`` flag.
  <details><summary>full docstring</summary>

  :param doc_id: Integer id of the intel record.
  :param body: Partial update dict; accepted key: ``dismissed`` (bool).
  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `get_briefing()` — Return the latest cached briefing, or an empty response if none exists.
  <details><summary>full docstring</summary>

  :return: Briefing dict with ``generated_at`` and ``sections``, or ``{}``.
  :rtype: dict
  </details>
- `generate_briefing(background_tasks: BackgroundTasks)` — Trigger briefing generation in the background.
  <details><summary>full docstring</summary>

  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `generate_passdown(body: dict | None=None)` — Build a passdown suggestion from recent activity.
  <details><summary>full docstring</summary>

  Stateless — nothing is written to the DB.  The caller is expected to edit
  the HTML before sending.

  :param body: Optional dict with ``hours`` (int, default 12).
  :return: ``{"generated_at", "hours", "sections", "html"}``.
  </details>
- `get_analyses(source: str | None=None, category: str | None=None, hierarchy: str | None=None, project: str | None=None, q: str | None=None, from_date: str | None=None, to_date: str | None=None, limit: int=1000)` — Return stored analysis records with optional filtering.
  <details><summary>full docstring</summary>

  All filters are applied sequentially (AND logic).  Results are sorted by
  ``timestamp`` descending.  JSON-encoded fields are deserialized via
  ``_deserialize_analysis`` before returning.

  :param source: Filter to a specific connector.
  :param category: Filter by category.
  :param hierarchy: Filter by hierarchy tier.
  :param project: Filter by project tag.  Pass ``"__none__"`` to return only
                  untagged items.
  :param q: Full-text search across ``title``, ``summary``, ``author``, and
            ``body_preview`` (case-insensitive substring match).
  :param from_date: ISO 8601 lower bound on ``timestamp`` (inclusive).
  :param to_date: ISO 8601 upper bound on ``timestamp`` (inclusive).
  :param limit: Maximum number of results to return. Defaults to 1000.
  :return: List of deserialized analysis dicts sorted newest-first.
  :rtype: list[dict]
  </details>
- `get_situations(project: str | None=None, status: str | None=None, lifecycle_status: str | None=None, min_score: float=0.0, include_dismissed: bool=False, include_resolved: bool=False)` — Return situations, filtered and sorted by score descending.
  <details><summary>full docstring</summary>

  Default view: ``new``, ``investigating``, and ``waiting`` situations.
  Pass ``include_resolved=true`` to also show ``resolved``.
  Pass ``include_dismissed=true`` to also show ``dismissed``.
  Pass ``lifecycle_status=<value>`` to filter to an exact lifecycle status.
  </details>
- `get_situation(situation_id: str)` — Return a single situation with all contributing analyses fully deserialized.
  <details><summary>full docstring</summary>

  :param situation_id: UUID of the situation to retrieve.
  :return: Full situation dict with deserialized ``items`` list.
  :raises HTTPException 404: If no situation with the given ID exists.
  </details>
- `dismiss_situation(situation_id: str, body: dict | None=None)` — Mark a situation as dismissed.
  <details><summary>full docstring</summary>

  :param situation_id: UUID of the situation to dismiss.
  :param body: Optional dict with a ``reason`` key.
  :return: ``{"ok": True}``
  :raises HTTPException 404: If no situation with the given ID exists.
  </details>
- `undismiss_situation(situation_id: str)` — Restore a previously dismissed situation.
  <details><summary>full docstring</summary>

  :param situation_id: UUID of the situation to restore.
  :return: ``{"ok": True}``
  :raises HTTPException 404: If no situation with the given ID exists.
  </details>
- `rescore_situation(situation_id: str)` — Manually trigger a full score recomputation and LLM re-synthesis for a situation.
  <details><summary>full docstring</summary>

  :param situation_id: UUID of the situation to rescore.
  :return: Updated situation response dict.
  :raises HTTPException 404: If no situation with the given ID exists.
  </details>
- `split_situation_endpoint(situation_id: str, body: dict)` — Move a subset of items out of ``situation_id`` into a new situation.
  <details><summary>full docstring</summary>

  :param body: ``{"item_ids": ["..."], "new_title": "<optional>"}``
  :return: ``{"ok": True, "new_situation_id": "...", "original_situation_id": "..."}``
  :raises HTTPException 400: If validation fails (empty subset, unknown ids,
                             would empty the source).
  :raises HTTPException 404: If the situation does not exist.
  </details>
- `merge_situation_endpoint(situation_id: str, body: dict)` — Merge ``source_situation_id`` into ``situation_id`` (target).
  <details><summary>full docstring</summary>

  :param body: ``{"source_situation_id": "..."}``
  :return: ``{"ok": True, "situation_id": "<target>"}``
  :raises HTTPException 400: If validation fails (target == source, ids missing).
  :raises HTTPException 404: If either situation does not exist.
  </details>
- `patch_situation(situation_id: str, body: dict)` — Manually override editable fields on a situation record.
  <details><summary>full docstring</summary>

  Only ``title``, ``status``, and ``project_tag`` may be changed this way.

  :param situation_id: UUID of the situation to update.
  :param body: Partial update dict; accepted keys: ``title``, ``status``,
               ``project_tag``.
  :return: ``{"ok": True}`` plus all fields that were applied.
  :raises HTTPException 400: If no valid fields are present in ``body``.
  :raises HTTPException 404: If no situation with the given ID exists.
  </details>
- `transition_situation(situation_id: str, body: dict)` — Transition a situation to a new lifecycle status and log the event.
  <details><summary>full docstring</summary>

  :param body: ``{"to_status": "<status>", "note": "<optional note>",
                  "follow_up_date": "<optional ISO date>"}``
  :return: ``{"ok": True, "lifecycle_status": "<new status>"}``
  :raises HTTPException 404: If no situation exists.
  :raises HTTPException 422: If ``to_status`` is invalid.
  </details>
- `get_situation_events(situation_id: str)` — Return the lifecycle event history for a situation, oldest first.
  <details><summary>full docstring</summary>

  :param situation_id: UUID of the situation.
  :return: List of event dicts with ``from_status``, ``to_status``,
           ``timestamp``, and ``note``.
  :raises HTTPException 404: If no situation exists.
  </details>
- `submit_deep_analysis(situation_id: str)` — Submit a situation for extended-context deep analysis via merLLM's batch API.
  <details><summary>full docstring</summary>

  Builds a prompt from the situation's title, summary, and contributing items,
  then queues it on merLLM's background priority bucket so it drains behind
  any chat/short/feedback traffic but still uses the full reasoning model
  (qwen3:32b, 32K+ context).

  :param situation_id: UUID of the situation to analyse.
  :return: ``{"ok": True, "job_id": "..."}``
  :raises HTTPException 404: If no situation with the given ID exists.
  :raises HTTPException 502: If merLLM is unreachable.
  </details>
- `save_deep_analysis(situation_id: str, body: dict)` — Store a completed merLLM batch result as an intel item.
  <details><summary>full docstring</summary>

  The result is fetched from merLLM and linked to the situation.

  :param situation_id: UUID of the situation.
  :param body: Must contain ``job_id``.
  :return: ``{"ok": True}``
  :raises HTTPException 404: If situation or job not found.
  :raises HTTPException 409: If job is not yet completed.
  :raises HTTPException 502: If merLLM is unreachable.
  </details>
- `proxy_batch_status(job_id: str)` — Proxy GET /api/batch/status/{job_id} to merLLM.
  <details><summary>full docstring</summary>

  :param job_id: Batch job UUID.
  :return: Job status dict from merLLM.
  :raises HTTPException 404: If job not found.
  :raises HTTPException 502: If merLLM is unreachable.
  </details>
- `get_stats()` — Return aggregate statistics for the dashboard summary bar.
  <details><summary>full docstring</summary>

  :return: Dict with counts and breakdowns.
  :rtype: dict
  </details>
- `slack_connect()` — Begin the Slack OAuth2 user-token flow.
  <details><summary>full docstring</summary>

  :return: HTTP 302 redirect to the Slack authorization page.
  :raises HTTPException 400: If ``SLACK_CLIENT_ID`` is not yet configured.
  </details>
- `slack_callback(code: str=None, error: str=None, state: str=None)` — Handle the Slack OAuth2 redirect callback.
  <details><summary>full docstring</summary>

  :param code: Authorization code returned by Slack.
  :param error: Error identifier returned by Slack if the user denied access.
  :param state: CSRF state nonce generated in ``/slack/connect``.
  :return: HTTP 302 redirect.
  :raises HTTPException 400: If no ``code`` is provided and no ``error`` is set.
  :raises HTTPException 403: If the ``state`` parameter is missing or invalid.
  </details>
- `get_slack_workspaces()` — Return all connected Slack workspaces (without tokens).
  <details><summary>full docstring</summary>

  :return: List of dicts with ``team`` (display name) and ``team_id`` fields.
  :rtype: list[dict]
  </details>
- `disconnect_slack_workspace(team_id: str)` — Remove a Slack workspace's user token from stored settings.
  <details><summary>full docstring</summary>

  :param team_id: Slack workspace team ID to disconnect.
  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `teams_connect()` — Begin the Microsoft Teams (Azure AD) OAuth2 user-token flow.
  <details><summary>full docstring</summary>

  :return: HTTP 302 redirect to the Microsoft authorization page.
  :raises HTTPException 400: If ``TEAMS_CLIENT_ID`` is not yet configured.
  </details>
- `teams_callback(code: str=None, error: str=None, error_description: str=None, state: str=None)` — Handle the Microsoft Teams OAuth2 redirect callback.
  <details><summary>full docstring</summary>

  :param code: Authorization code returned by Microsoft.
  :param error: Error identifier returned if the user denied access.
  :param error_description: Human-readable error description.
  :param state: CSRF state nonce generated in ``/teams/connect``.
  :return: HTTP 302 redirect.
  :raises HTTPException 400: If no ``code`` is provided and no ``error`` is set.
  :raises HTTPException 403: If the ``state`` parameter is missing or invalid.
  </details>
- `get_teams_workspaces()` — Return all connected Microsoft Teams accounts (without tokens).
  <details><summary>full docstring</summary>

  :return: List of dicts with ``display_name``, ``account_id``, and
           ``tenant`` fields.
  :rtype: list[dict]
  </details>
- `disconnect_teams_account(account_id: str)` — Remove a Teams account's token bundle from stored settings.
  <details><summary>full docstring</summary>

  :param account_id: Microsoft Graph user ID of the account to disconnect.
  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `async seed_preview(request: Request)` — Start the seed state machine.
  <details><summary>full docstring</summary>

  Always succeeds immediately — the
  ``waiting_for_ingest`` phase handles empty databases by polling until
  items arrive.  Returns the current seed job state.
  </details>
- `async seed_update_context(request: Request)` — Update the user-provided context string on a waiting seed job.
  <details><summary>full docstring</summary>

  Accepted only while the job is in the ``waiting_for_ingest`` state.

  :param request: Request body must be JSON with a ``context`` key.
  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `seed_status()` — Return the current state of the background seed job.
  <details><summary>full docstring</summary>

  :return: Current seed job state dict.
  :rtype: dict
  </details>
- `seed_apply(body: dict, background_tasks: BackgroundTasks)` — Apply the seed editor's confirmed projects and topics to settings.
  <details><summary>full docstring</summary>

  :param body: Dict with keys ``projects`` (list), ``topics`` (list), and
               optionally ``retag`` (bool, default ``True``).
  :return: ``{"ok": True, "projects_added": N, "topics_added": M, "items_retagged": K}``
  :rtype: dict
  </details>
- `seed_run_scan()` — Run the optional post-seed connector scan.
  <details><summary>full docstring</summary>

  Transitions the seed state machine from ``scan_prompt`` to ``scanning``,
  runs a full multi-source scan, then transitions to ``done``.

  :return: ``{"ok": True}``
  :raises HTTPException 409: If a scan is already running.
  </details>
- `seed_skip_scan()` — Skip the optional post-seed connector scan.
  <details><summary>full docstring</summary>

  Transitions the seed state machine from ``scan_prompt`` straight to
  ``done`` without running a scan.

  :return: ``{"ok": True}``
  :rtype: dict
  </details>
- `merllm_status()` — Proxy GET /api/merllm/status from merLLM for the frontend status indicator.
- `list_contacts(query: str | None=None, limit: int=500)` — List contacts, most-recently-seen first, optionally filtered.
  <details><summary>full docstring</summary>

  :param query: Optional case-insensitive substring matched against name,
                employer, title, or any associated email.
  :param limit: Maximum rows to return (default 500).
  :return: ``{"contacts": [...], "total": N}``
  </details>
- `get_contact(contact_id: int)` — Fetch one contact, with all attached emails.
  <details><summary>full docstring</summary>

  :raises HTTPException 404: If no contact with ``contact_id`` exists.
  </details>
- `create_contact(body: dict)` — Manually create a new contact.
  <details><summary>full docstring</summary>

  Accepts: ``name``, ``phone``, ``employer``, ``title``, ``employer_address``,
  ``notes``, and an optional ``emails`` list.  The first email in the list
  becomes the primary.  Emails already attached to another contact are
  silently skipped — use the dedicated emails endpoint to merge.
  </details>
- `patch_contact(contact_id: int, body: dict)` — Update editable fields on a contact.  Unknown columns are silently dropped.
  <details><summary>full docstring</summary>

  Any editable field included in the request body is treated as a manual
  edit: its ``<field>_source`` is stamped ``manual`` and the field name is
  added to ``manually_edited_fields`` so the signature parser will never
  overwrite it later.  This is the contract that makes manual edits sticky
  against repeated re-parses (squire#31).

  :raises HTTPException 404: If no contact with ``contact_id`` exists.
  </details>
- `delete_contact(contact_id: int)` — Delete a contact and all of its email associations.
  <details><summary>full docstring</summary>

  :raises HTTPException 404: If no contact with ``contact_id`` exists.
  </details>
- `add_contact_email(contact_id: int, body: dict)` — Attach an email address to an existing contact.
  <details><summary>full docstring</summary>

  Body: ``{"email": "addr@host", "is_primary": false}``

  :raises HTTPException 404: If no contact with ``contact_id`` exists.
  :raises HTTPException 409: If the email is already attached to a different
                              contact (caller can merge manually).
  </details>
- `delete_contact_email(contact_id: int, email: str)` — Detach an email from a contact.
  <details><summary>full docstring</summary>

  Does not delete the contact even if this
  was its only email — that requires the contact-level DELETE.

  :raises HTTPException 404: If no contact with ``contact_id`` exists.
  </details>
- `rebuild_contacts()` — Rebuild the contacts table from every item's headers.
  <details><summary>full docstring</summary>

  Walks every existing item and (re)populates contacts from To/CC/author
  headers.  Idempotent — safe to re-run after schema changes or new bulk
  imports.

  :return: ``{"ok": True, "items_scanned": N, "contacts_touched": M, "total_contacts": K}``
  </details>
- `reparse_contact_signatures()` — Re-run the email-body signature parser across every item.
  <details><summary>full docstring</summary>

  Results are applied to the corresponding contact rows (squire#31).
  Manually-edited fields are never overwritten — see
  ``signatures.apply_to_contact``.

  Mirrors ``/contacts/rebuild`` but for the body-parsing pass.  Idempotent
  and safe to re-run.

  :return: ``{"ok": True, "items_scanned": N, "items_applied": M, "fields_written": K}``
  </details>
- `merllm_default_model()` — Proxy GET /api/merllm/default-model from merLLM.
- `lookahead_list_cards(project: str | None=None, start: str | None=None, end: str | None=None)` — List cards, optionally filtered by project tag and overlapping date window.
- `lookahead_get_card(card_id: str)` — Return one look-ahead card, with dependencies, links and resources.
  <details><summary>full docstring</summary>

  Responds 404 when the card does not exist.
  </details>
- `lookahead_create_card(body: dict)` — Create a look-ahead card and the todo that mirrors it.
  <details><summary>full docstring</summary>

  A card id may be supplied for idempotent creation; otherwise one is
  generated.  ``depends_on``, ``links`` and ``resources`` are applied only when
  present in the body, so an omitted key leaves that relation untouched rather
  than clearing it.  Every card is paired with a manual todo (single read path
  for the Todos tab, parsival#85), created here unless one is already linked.
  </details>
- `lookahead_update_card(card_id: str, body: dict)` — Patch a card and propagate the change to its mirrored todo.
  <details><summary>full docstring</summary>

  Only the fields present in the body are written.  Title, end date, project,
  assignee and status are mirrored onto the linked todo so the Todos tab and
  the board never disagree; a card that somehow has no todo gets one here.
  When the card belongs to a template instance, completing it may auto-complete
  that instance.  Responds 404 when the card does not exist.
  </details>
- `lookahead_delete_card(card_id: str)` — Delete a card and the todo mirroring it.
  <details><summary>full docstring</summary>

  The todo id is read before the card is removed, because the link row goes
  with the card via cascade.  Deleting an absent card is a no-op.
  </details>
- `lookahead_set_card_resource_status(card_id: str, resource_id: int, body: dict)` — Set the BOM fulfilment status of one resource on one card.
  <details><summary>full docstring</summary>

  Validated against ``db._RESOURCE_STATUSES`` and rejected with 400 rather than
  stored, so the board's status cycle cannot write an unknown value.  Returns
  the whole refreshed card so the caller can re-render without a second fetch.
  </details>
- `lookahead_list_resources(type: str | None=None)` — List the global resource catalog, optionally filtered by ``type``.
- `lookahead_create_resource(body: dict)` — Add a resource to the global catalog.
  <details><summary>full docstring</summary>

  ``name`` is required and ``type`` defaults to ``person``; both are validated
  here so a bad request returns 400 instead of a ValueError traceback.
  </details>
- `lookahead_update_resource(resource_id: int, body: dict)` — Patch a catalog resource's name, type or notes.
  <details><summary>full docstring</summary>

  An invalid type surfaces as 400 and a missing resource as 404, rather than
  either escaping as a 500.
  </details>
- `lookahead_delete_resource(resource_id: int)` — Remove a resource from the global catalog.
  <details><summary>full docstring</summary>

  Its per-card assignments are removed with it by cascade, so cards that
  referenced it simply lose that requirement.
  </details>
- `lookahead_list_shifts(project: str | None=None)` — List per-project shift schedules, optionally for one project only.
- `lookahead_upsert_shift(project_tag: str, shift_num: int, body: dict)` — Create or replace one shift in a project's schedule.
  <details><summary>full docstring</summary>

  Shift numbers are capped at 1-3 to match the board's three-shift day; a
  higher number is rejected with 400 rather than stored and silently unrendered.
  </details>
- `lookahead_delete_shift(project_tag: str, shift_num: int)` — Remove one shift from a project's schedule.
  <details><summary>full docstring</summary>

  Cards already scheduled on that shift number are left alone -- the shift row
  only supplies the label and hours the board draws.
  </details>
- `lookahead_overview(start: str | None=None, end: str | None=None)` — Return one row per project with cards overlapping the window.
  <details><summary>full docstring</summary>

  Empty-window projects are omitted so the UI can render only rows that
  actually have activity.  Rows are sorted by the soonest card's start_date.
  </details>
- `lookahead_list_templates(owner: str | None=None)` — List work-package templates, each with its task graph attached.
- `lookahead_get_template(template_id: str)` — Return one template with its tasks, dependencies and resource needs.
  <details><summary>full docstring</summary>

  Responds 404 when the template does not exist.
  </details>
- `lookahead_create_template(body: dict)` — Create a work-package template and its task graph.
  <details><summary>full docstring</summary>

  An id may be supplied for idempotent creation; otherwise one is generated.
  The body is validated up front and again by the DB layer, whose ValueError is
  translated to 400 so a malformed task graph never surfaces as a 500.
  </details>
- `lookahead_update_template(template_id: str, body: dict)` — Patch a template, replacing its task graph when ``tasks`` is supplied.
  <details><summary>full docstring</summary>

  Editing a template does not touch instances already created from it -- use
  the per-instance upgrade endpoint for that (parsival#60).  Invalid input is
  400, a missing template 404.
  </details>
- `lookahead_delete_template(template_id: str)` — Delete a template and its task graph.
  <details><summary>full docstring</summary>

  Cards already instantiated from it are deliberately kept: they are real
  scheduled work, and removing the pattern should not erase them.
  </details>
- `lookahead_instantiate_template(template_id: str, body: dict)` — Instantiate a template into concrete cards from ``start_date``.
  <details><summary>full docstring</summary>

  Task offsets are resolved to absolute dates and every created card is stamped
  with the new instance id, so a later reschedule can move the whole cohort by
  one delta.  ``start_date`` is required; ``project_tag`` is required too, but
  may come from the template's default rather than the body.
  </details>
- `lookahead_list_instances(project: str | None=None, status: str | None=None)` — List template instances, optionally filtered by project and status.
  <details><summary>full docstring</summary>

  Rows carry the outdated flag the UI renders as an "upgrade available" badge.
  </details>
- `lookahead_get_instance(instance_id: str)` — Return one template instance together with its cohort of cards.
  <details><summary>full docstring</summary>

  Responds 404 when the instance does not exist.
  </details>
- `lookahead_update_instance(instance_id: str, body: dict)` — Reschedule an instance and/or move it to a new status.
  <details><summary>full docstring</summary>

  A new ``start_date`` shifts every card in the cohort by the same delta rather
  than re-deriving them, which preserves any manual per-card adjustments.  An
  invalid status is 400; a missing instance is 404.
  </details>
- `lookahead_delete_instance(instance_id: str)` — Delete a template instance and the cards it created.
- `lookahead_upgrade_instance(instance_id: str)` — Re-apply the latest template version to this instance (parsival#60).
  <details><summary>full docstring</summary>

  Opt-in: the UI surfaces an "outdated" badge from
  ``GET /lookahead/instances`` and the user clicks Upgrade per instance.
  </details>
- `lookahead_detach_card(card_id: str)` — Detach a card from its template instance, leaving it standalone.
  <details><summary>full docstring</summary>

  Used when one task of an instantiated work package diverges: clearing the
  instance stamp exempts the card from cohort reschedules and upgrades while
  keeping the card itself.  Responds 404 when the card does not exist.
  </details>
- `lookahead_list_suggestions(card_id: str, include_decided: bool=False)` — List the LLM's proposed cross-system links for one card.
  <details><summary>full docstring</summary>

  Pending proposals only by default; ``include_decided`` also returns ones
  already accepted or rejected.  Item suggestions are enriched here with the
  target's title, source and url so the UI can render the list without a
  round-trip per row.  Responds 404 when the card does not exist.
  </details>
- `lookahead_annotate_card(card_id: str)` — Run the LLM annotator synchronously for one card.
- `lookahead_annotate_project(body: dict)` — Bulk-annotate every card in a project's current window.
  <details><summary>full docstring</summary>

  Runs synchronously one card at a time.  The caller controls the blast
  radius via the window parameters so an entire project isn't scanned by
  accident.
  </details>
- `lookahead_accept_suggestion(suggestion_id: int)` — Accept an LLM link proposal, promoting it to a real card link.
  <details><summary>full docstring</summary>

  Deciding an already-decided suggestion is a 400; an unknown id is a 404.
  </details>
- `lookahead_reject_suggestion(suggestion_id: int)` — Reject an LLM link proposal, leaving the card's links unchanged.
  <details><summary>full docstring</summary>

  The rejection is recorded rather than deleted so the annotator does not keep
  re-proposing the same pairing.  Re-deciding is a 400; an unknown id a 404.
  </details>
- `list_lancellmot_aliases_route()` — List all parsival-project → lancellmot-project aliases (Settings audit).
- `put_lancellmot_alias(payload: dict)` — Upsert a single project-tag → lancellmot-project alias.
- `delete_lancellmot_alias_route(parsival_project: str)` — Remove a project-tag alias.
- `get_lancellmot_projects()` — Proxy lancellmot's project list (populates the Settings dropdown).
- `docs_for_tag(tag: str, limit: int=5)` — Resolve a project tag to its lancellmot documents for the card chip.
  <details><summary>full docstring</summary>

  Returns one of three shapes the UI renders as distinct chip states:
  ``ok`` (resolved, with docs), ``unmapped`` (no alias), or ``unreachable``
  (lancellmot down). The DB lookup holds ``db.lock``; the network call does
  not, so a slow lancellmot never blocks other DB work.
  </details>

### `api.attention`
*attention.py — Adaptive attention model for Parsival.*

- `is_cold_start() -> bool` — Return True when fewer than COLD_START_THRESHOLD actions are recorded.
- `record_action(item_id: str, action_type: str) -> None` — Log a user interaction with an item.
- `compute_score(item_embedding: list) -> float` — Return attention score in [0, 1].  Returns 0.5 on cold start or unavailability.
- `get_why(item_embedding: list) -> str` — Return a human-readable explanation for the attention score.
- `get_summary() -> dict` — Return a summary dict for the merLLM 'My Day' panel.
  <details><summary>full docstring</summary>

  Includes high-attention item count, cold-start flag, and centroid freshness.
  </details>

### `api.config`
*config.py — Runtime configuration for the Squire API.*

- `SLACK_USER_TOKENS: list[dict] = []` — (undocumented)
- `TEAMS_USER_TOKENS: list[dict] = []` — (undocumented)
- `FOCUS_TOPICS: list[str] = [t.strip() for t in _ft.split(',') if t.strip()] if _ft else []` — (undocumented)
- `PROJECTS: list[dict]` — (undocumented)
- `NOISE_KEYWORDS: list[str] = []` — (undocumented)
- `TASK_KEYWORDS: list[str] = []` — (undocumented)
- `APPROVAL_KEYWORDS: list[str] = []` — (undocumented)
- `FYI_KEYWORDS: list[str] = []` — (undocumented)
- `ASSIGNMENT_CORRECTIONS: list[dict] = []` — (undocumented)
- `PRIORITY_OVERRIDES: list[dict] = []` — (undocumented)
- `apply_overrides(d: dict) -> None` — Hot-reload config from a saved-settings dict without restarting the container.
  <details><summary>full docstring</summary>

  Called on startup (if saved settings exist in the DB) and after every
  successful ``POST /settings`` or OAuth callback.  Handles all string
  credential fields, ``slack_user_tokens``, ``teams_user_tokens``,
  ``slack_channels``, ``focus_topics``, ``projects``, ``noise_keywords``,
  and ``lookback_hours``.

  :param d: Dict of setting key/value pairs, as stored in the ``settings``
            TinyDB table or posted by the frontend.
  :type d: dict
  </details>
- `effective_model() -> str` — Return the model name to use for analysis, respecting escalation config.
- `ollama_headers(priority: str | None=None) -> dict` — Build request headers for Ollama API calls.
  <details><summary>full docstring</summary>

  Includes Cloudflare Access service token headers when both
  ``CF_CLIENT_ID`` and ``CF_CLIENT_SECRET`` are configured, allowing
  requests to pass through a Cloudflare Access policy protecting the
  Ollama endpoint.

  :param priority: Optional merLLM priority bucket name (one of ``chat``,
      ``embeddings``, ``short``, ``feedback``, ``background``). When set,
      an ``X-Priority`` header is added so merLLM places the request in
      the right bucket. When ``None``, no header is sent and merLLM
      applies its own back-compat default. Note that ``embeddings``
      is auto-routed by merLLM at the ``/api/embeddings`` endpoint —
      callers should not need to set it explicitly (merLLM#38).
  :return: Dict of HTTP headers to include with every Ollama request.
  :rtype: dict
  </details>
- `validate() -> list[str]` — Return a list of warnings for missing or placeholder configuration values.
  <details><summary>full docstring</summary>

  Used by the ``/health`` and ``/settings`` endpoints to surface
  integration issues to the frontend without raising exceptions.

  :return: List of human-readable warning strings, empty if fully configured.
  :rtype: list[str]
  </details>

### `api.connector_github`
*connector_github.py — GitHub data connector.*

- `fetch() -> list[RawItem]` — Fetch actionable GitHub items for the configured user.
  <details><summary>full docstring</summary>

  Skips gracefully if ``config.GITHUB_PAT`` is absent or still set to the
  placeholder value.

  :return: List of raw items covering notifications, review requests, and
           assigned issues within the lookback window.
  :rtype: list[RawItem]
  </details>

### `api.connector_jira`
*connector_jira.py — Jira Cloud data connector.*

- `fetch() -> list[RawItem]` — Fetch Jira issues matching the configured JQL query.
  <details><summary>full docstring</summary>

  Skips gracefully if credentials or domain are absent or still set to
  placeholder values.  All returned issues are included regardless of the
  lookback window — Jira open tickets are always surfaced since their
  relevance is determined by status, not recency.

  :return: List of raw items, one per Jira issue.
  :rtype: list[RawItem]
  </details>

### `api.connector_outlook`
*connector_outlook.py — Outlook connector stub.*

- `fetch() -> list[RawItem]` — Return an empty list — Outlook ingestion is handled by the host sidecar.
  <details><summary>full docstring</summary>

  :return: Empty list.
  :rtype: list[RawItem]
  </details>

### `api.connector_slack`
*connector_slack.py — Slack data connector.*

- `fetch() -> list[RawItem]` — Fetch Slack items across all configured workspaces.
  <details><summary>full docstring</summary>

  Prefers per-user OAuth tokens stored in ``config.SLACK_USER_TOKENS``.
  Falls back to the legacy bot token path if no user tokens are present.

  :return: Combined list of raw items from all workspaces, deduplicated
           within each workspace by item ID.
  :rtype: list[RawItem]
  </details>

### `api.connector_teams`
*connector_teams.py — Microsoft Teams data connector.*

- `fetch() -> list[RawItem]` — Fetch Teams items across all connected accounts.
  <details><summary>full docstring</summary>

  :return: Combined list of raw items from all accounts, deduplicated
           within each account by item ID.
  :rtype: list[RawItem]
  </details>

### `api.contacts`
*contacts.py — Contacts table population from email headers.*

- `parse_header_pairs(field: str) -> list[tuple[str, str]]` — Pull ``(display_name, email)`` tuples from a raw header string.
  <details><summary>full docstring</summary>

  Picks up RFC-style pairs like ``"Jane Doe <jane@acme.com>"`` first, then
  falls back to bare addresses (no display name) for the rest.  Emails are
  lowercased; display names are stripped of surrounding whitespace and
  quotes.
  </details>
- `scrape_item_headers(item: dict) -> int` — Upsert every contact found in an item's author/to/cc fields.
  <details><summary>full docstring</summary>

  Designed to be called from the analysis save path.  Failures are logged
  but never raised — contact scraping must not break ingestion.

  Returns the number of contact rows touched (created or bumped).
  </details>
- `rebuild_from_items(items: Iterable[dict] | None=None) -> dict` — Walk every item (or a provided subset) and populate contacts.
  <details><summary>full docstring</summary>

  Idempotent — re-running on the same corpus will bump source counts but
  will not duplicate rows because emails are unique in contact_emails.

  Returns a summary dict with ``items_scanned`` and ``contacts_touched``.
  </details>

### `api.correlator`
*correlator.py — Cross-source situation correlation and synthesis.*

- `extract_references(title: str, body: str) -> list` — Extract explicit cross-source identifiers from item title and body.
  <details><summary>full docstring</summary>

  Returns a deduplicated lowercase list, e.g. ["proj-142", "pr-89"].
  </details>
- `find_correlated_candidates(item_id: str, references: list, vector: list, project_tag, all_analyses: list, similarity_threshold: float=0.82) -> list` — Return item_ids of analyses likely describing the same situation.
  <details><summary>full docstring</summary>

  Two-pass approach:
  1. Deterministic: all analyses sharing at least one reference string.
  2. Semantic: analyses whose stored vector has cosine similarity
     >= similarity_threshold to this item's vector, within the same
     project_tag (or both untagged).

  ``all_analyses`` must be provided by the caller (pre-fetched under db_lock)
  to avoid opening a second TinyDB instance concurrently with the main app.

  Returns deduplicated list of item_ids excluding the query item itself.
  </details>
- `score_situation(item_ids: list, analyses: list) -> float` — Compute a composite urgency score for a candidate situation cluster.
  <details><summary>full docstring</summary>

  Components:
    source_score    (0.35) — log2(unique_source_types + 1)
    recency_score   (0.25) — 1 / (1 + hours_since_latest / 12)
    priority_score  (0.25) — max individual priority, normalized 0-1
    addressal_score (0.15) — proportion of user-hierarchy items, amplified

  Returns float in roughly 0.0–2.5 range.
  </details>
- `synthesize_situation(item_records: list, user_name: str, intel_items: list=None, completed_actions: list=None) -> dict` — Call Ollama to produce a cross-source narrative for a situation cluster.
  <details><summary>full docstring</summary>

  Falls back to a minimal dict on failure.

  items_block: per-item summary lines capped at 6 items × 200 chars each.
  intel_items: optional list of information_items dicts from the intel table.
  completed_actions: optional list of done todo dicts (parsival#56) so the
      narrative reflects work that is already finished instead of treating
      every action item as still pending.
  </details>

### `api.crypto`
*crypto.py — At-rest encryption for connection credentials.*

- `SECRET_FIELDS: frozenset[str] = frozenset({'password', 'client_secret', 'token'})` — (undocumented)
- `encrypt_secret(plaintext: str) -> str` — Encrypt *plaintext* with Fernet if a key is configured.
  <details><summary>full docstring</summary>

  Returns *plaintext* unchanged when no key is set (pass-through mode).
  Values that already look encrypted are returned unchanged.
  </details>
- `decrypt_secret(value: str) -> str` — Decrypt *value* if it is a Fernet token and a key is configured.
  <details><summary>full docstring</summary>

  Returns *value* unchanged when no key is set or the value is plaintext.
  </details>
- `encrypt_config(cfg: dict) -> dict` — Return a copy of *cfg* with all SECRET_FIELDS values encrypted.
- `decrypt_config(cfg: dict) -> dict` — Return a copy of *cfg* with all SECRET_FIELDS values decrypted.

### `api.db`
*db.py — SQLite database layer for Squire.*

- `parse_project_tags(val) -> list[str]` — Parse a project_tag column value into a list of project names.
  <details><summary>full docstring</summary>

  Handles: None → [], bare string → [string], JSON array string → list.
  </details>
- `serialize_project_tags(tags) -> str | None` — Serialize a list of project names to a JSON array string for storage.
  <details><summary>full docstring</summary>

  Returns None for empty lists (column stays NULL for untagged items).
  </details>
- `item_has_project(item: dict, project: str) -> bool` — Check whether an item record is tagged to a given project.
- `item_has_any_project(item: dict) -> bool` — Check whether an item has any project tag at all.
- `conn() -> sqlite3.Connection` — Return the shared SQLite connection, creating it on first call.
- `backfill_manual_todo_items() -> int` — Backfill placeholder items rows for legacy manual todos.
  <details><summary>full docstring</summary>

  Covers todos that predate the synthesized-item model (is_manual=1,
  item_id IS NULL).

  Each orphan todo gets item_id='manual_<todo_id>' on both the todo and
  a synthesized items row whose title/body seed from the todo description
  so the detail panel has something to render.

  Idempotent: runs only against todos still missing item_id.

  Returns the number of rows backfilled.
  </details>
- `get_item(item_id: str) -> dict | None` — Fetch a single item by item_id.
- `get_all_items() -> list[dict]` — Return all item records.
- `get_items_by_project(project_tag: str) -> list[dict]` — Return all items tagged to a project (handles both single and multi-tag storage).
- `get_items_by_conversation(conversation_id: str) -> list[dict]` — Return all items in a conversation thread, ordered by timestamp.
- `get_items_by_situation(situation_id: str) -> list[dict]` — Return all items linked to a situation.
- `upsert_item(data: dict) -> None` — Insert or replace an item record.
  <details><summary>full docstring</summary>

  All JSON columns (action_items, goals, key_dates, information_items,
  references) are serialised if they arrive as Python objects.
  </details>
- `update_item(item_id: str, updates: dict) -> None` — Apply a partial update to an item row.
- `update_items_by_project(project_tag: str, updates: dict) -> None` — Apply a partial update to all items tagged to a project (handles multi-tag).
  <details><summary>full docstring</summary>

  When updates contains project_tag=None, removes the given tag from multi-tag
  items rather than NULLing the whole column.
  </details>
- `count_items() -> int` — Return total item count.
- `get_items_with_pending_batch() -> list[dict]` — Return all items that have a batch_job_id set (awaiting batch result).
- `set_batch_job_id(item_id: str, batch_job_id: str | None) -> None` — Set or clear the batch_job_id on an item row.
- `get_todos(done: bool=False, source: str | None=None, priority: str | None=None, project_tag: str | None=None) -> list[dict]` — Return todo rows with optional filters, sorted by priority then created_at.
- `get_todos_for_item(item_id: str) -> list[dict]` — Return all todos linked to a specific item.
- `todo_exists(item_id: str, description: str) -> bool` — Check if a todo with this item_id+description already exists.
- `todo_exists_in_conversation(conversation_id: str, description: str) -> bool` — True if any item in this conversation already has a matching todo.
  <details><summary>full docstring</summary>

  A single Outlook reply chain shares one conversation_id across many
  item_ids, so per-item todo_exists misses duplicates the LLM re-emits on
  each reply. Widens the scope to the thread and normalizes descriptions.
  </details>
- `get_open_todos_for_conversation(conversation_id: str | None, before_timestamp: str | None=None, limit: int=15) -> list[dict]` — Return open todos saved for earlier items in this conversation.
  <details><summary>full docstring</summary>

  Feeds the LLM a "do not re-emit these" hint when analyzing the next
  message in a thread (parsival#79). Paraphrase-level dedup the exact /
  normalized check in todo_exists_in_conversation cannot catch.

  Scoped by ``items.conversation_id``; filtered to ``todos.done = 0``.
  When ``before_timestamp`` is set, only todos from items with a strictly
  earlier ``items.timestamp`` are returned — required on reanalyze so a
  message does not self-suppress its own todos. Most recent ``limit``
  items win (prompt-bloat guard on very long threads).
  </details>
- `insert_todo(data: dict) -> int` — Insert a todo row and return its auto-generated id.
- `update_todo(todo_id: int, updates: dict) -> None` — Apply a partial update to a todo row by id.
- `update_todos_for_item(item_id: str, updates: dict) -> None` — Apply a partial update to all todos linked to an item.
- `delete_todos_for_item(item_id: str) -> None` — Remove all todos linked to an item.
- `delete_todo_by_id(todo_id: int) -> None` — Remove a single todo by its integer id.
- `delete_item_by_id(item_id: str) -> None` — Remove a single items row by its item_id.
- `get_all_todos() -> list[dict]` — Return all todo rows (including done), ordered by priority then created_at.
- `get_todo_by_id(todo_id: int) -> dict | None` — Fetch a single todo by its integer id.
- `count_assigned_open() -> int` — Return the number of open todos in the 'assigned' state with a non-empty assigned_to.
  <details><summary>full docstring</summary>

  Uses the idx_todos_status(done, status) index to avoid pulling full rows —
  the Assigned vtab badge only needs the count, not the payload.
  </details>
- `intel_exists(item_id: str, fact: str) -> bool` — Check if an intel row with this item_id+fact already exists.
- `insert_intel(data: dict) -> None` — Insert an intel row.
- `get_intel_for_item(item_id: str) -> list[dict]` — Return all intel rows for an item.
- `get_intel_for_items(item_ids: list) -> list[dict]` — Return all non-dismissed intel rows for a set of item_ids.
- `get_all_intel(dismissed: bool=False) -> list[dict]` — Return all intel rows, optionally including dismissed ones.
- `delete_intel_for_item(item_id: str) -> None` — Remove all intel rows for an item.
- `delete_intel_by_id(intel_id: int) -> None` — Remove a single intel row by its integer id.
- `update_intel_by_id(intel_id: int, updates: dict) -> None` — Apply a partial update to an intel row by id.
- `update_intel_project(item_id: str, project_tag) -> None` — Sync project_tag on all intel rows for an item.
  <details><summary>full docstring</summary>

  Accepts a single tag string, a list of tags, or a serialized JSON array.
  Intel rows store a single tag (the first/primary), since each intel fact
  typically belongs to one project context.
  </details>
- `get_situation(situation_id: str) -> dict | None` — Fetch a single situation by situation_id, with JSON list columns parsed.
- `get_all_situations(include_dismissed: bool=False) -> list[dict]` — Return all situations with JSON list columns parsed.
- `insert_situation(data: dict) -> None` — Insert a new situation record.
- `update_situation(situation_id: str, updates: dict) -> None` — Apply a partial update to a situation record.
- `delete_situation(situation_id: str) -> None` — Delete a situation record.
- `get_situations_containing_item(item_id: str) -> list[dict]` — Return all situations (including dismissed) where item_ids contains item_id.
- `get_active_situations(lifecycle_statuses: list[str] | None=None) -> list[dict]` — Return situations filtered by lifecycle_status.
  <details><summary>full docstring</summary>

  If ``lifecycle_statuses`` is None, defaults to active statuses:
  ``new``, ``investigating``, ``waiting``.
  </details>
- `insert_situation_event(situation_id: str, from_status: str | None, to_status: str, note: str | None=None) -> None` — Log a lifecycle status transition event.
- `get_situation_events(situation_id: str) -> list[dict]` — Return all lifecycle events for a situation, oldest first.
- `record_user_action(item_id: str, action_type: str) -> None` — Log a user interaction with an item for attention model training.
- `get_user_actions(since_iso: str | None=None) -> list[dict]` — Return user actions, optionally filtered to those after *since_iso*.
- `count_user_actions() -> int` — Return total number of recorded user actions.
- `get_model_state(key: str) -> dict | None` — Return a deserialized model state value, or None.
- `set_model_state(key: str, value: dict) -> None` — Upsert a model state value (serialized as JSON).
- `upsert_lancellmot_alias(parsival_project: str, lancellmot_project_id: str, lancellmot_project_name: str) -> None` — Create or update the lancellmot mapping for a parsival project name.
- `get_lancellmot_alias_for_tag(parsival_project: str) -> dict | None` — Return the alias row for a project name, or None if unmapped.
- `list_lancellmot_aliases() -> list[dict]` — Return all alias rows ordered by parsival project name.
- `delete_lancellmot_alias(parsival_project: str) -> None` — Remove the alias for a project name; a no-op if it does not exist.
- `get_settings() -> dict` — Return the settings blob as a dict.
- `save_settings(data: dict) -> None` — Persist the settings blob (upsert on id=1).
- `save_briefing(content: dict) -> None` — Persist the latest briefing, replacing any previous one.
- `get_briefing() -> dict | None` — Return the latest briefing, or None if none exists.
- `insert_scan_log(data: dict) -> None` — Insert a scan log entry.
- `get_scan_logs(limit: int=20) -> list[dict]` — Return the most recent scan log entries.
- `get_all_scan_logs() -> list[dict]` — Return all scan log entries, newest first.
- `get_embedding(project: str) -> dict | None` — Return the embedding record for a project.
- `upsert_embedding(project: str, items: list, centroids: dict, centroid_counts: dict) -> None` — Upsert the embedding record for a project.
- `get_all_embeddings() -> list[dict]` — Return all embedding records with JSON columns parsed.
- `delete_embedding_project(project: str) -> None` — Remove the embedding record for a project.
- `reset_data_tables() -> None` — Truncate all data tables while preserving settings.
- `upsert_node(node_id: str, node_type: str, label: str, properties: dict=None) -> None` — Insert or update a graph node.
- `upsert_edge(src_id: str, dst_id: str, edge_type: str, weight: float=1.0, properties: dict=None) -> None` — Insert or update a graph edge.
  <details><summary>full docstring</summary>

  Weight is updated to the new value if the
  edge already exists (e.g. accumulated co-occurrence count).
  </details>
- `get_edges_from(node_id: str, edge_type: str | None=None) -> list[dict]` — Return all edges where src_id matches.
- `get_edges_to(node_id: str, edge_type: str | None=None) -> list[dict]` — Return all edges where dst_id matches.
- `get_node(node_id: str) -> dict | None` — Fetch a single node by id.
- `get_nodes_by_type(node_type: str) -> list[dict]` — Return all nodes of a given type.
- `get_contact(contact_id: int) -> dict | None` — Fetch a single contact by id, with emails attached.
- `get_contact_by_email(email: str) -> dict | None` — Look up a contact by any of its email addresses (case-insensitive).
- `find_contacts_by_name(name: str) -> list[dict]` — Substring (case-insensitive) match on contact name. Used by owner resolution.
- `list_contacts(query: str | None=None, limit: int=500) -> list[dict]` — Return contacts ordered by most-recently-seen, optionally filtered.
  <details><summary>full docstring</summary>

  `query` matches against name, employer, title, or any associated email
  (case-insensitive substring).
  </details>
- `count_contacts() -> int` — Return total contact count.
- `insert_contact(data: dict) -> int` — Insert a new contact row and return its assigned contact_id.
  <details><summary>full docstring</summary>

  `data` may contain any contact column plus an optional "emails" list.
  Emails are inserted into contact_emails; the first becomes primary.

  Manually-created contacts (``is_manual=True``) default every field's
  provenance to ``manual`` and seed ``manually_edited_fields`` with the
  fields the caller actually populated, so the signature parser will not
  later clobber what the user typed in by hand.
  </details>
- `update_contact(contact_id: int, updates: dict) -> None` — Apply a partial update to a contact row.  Ignores unknown columns.
  <details><summary>full docstring</summary>

  JSON-typed columns (``manually_edited_fields``, ``signature_confidence``)
  are serialised here when callers pass list/dict values.  Source columns
  are passed through verbatim — callers like the signature parser stamp
  them explicitly, and the manual PATCH path in app.py adds its own
  'manual' stamping wrapper around this helper.
  </details>
- `delete_contact(contact_id: int) -> None` — Delete a contact and all its emails (cascade via FK).
- `add_contact_email(contact_id: int, email: str, is_primary: bool=False) -> bool` — Attach an email to a contact.
  <details><summary>full docstring</summary>

  Returns False if the email is already
  attached to another contact (caller can decide whether to merge).
  </details>
- `remove_contact_email(contact_id: int, email: str) -> None` — Detach an email from a contact.
- `upsert_contact_from_header(display_name: str, email: str, item_id: str | None=None, item_timestamp: str | None=None) -> int` — Idempotently record a contact seen in an email header.
  <details><summary>full docstring</summary>

  Lookup is by email (the only stable thing we have in a header).  When the
  email is new, a contact is created using the display name.  When the email
  already exists, source_count and last_seen are bumped, and the name is
  filled in if it was previously empty.

  Returns the contact_id.
  </details>
- `get_lookahead_card(card_id: str) -> dict | None` — Return one card with its dependencies, links and resources hydrated.
  <details><summary>full docstring</summary>

  :param card_id: The card's UUID.
  :return: The card dict, or ``None`` if no such card exists.
  </details>
- `list_lookahead_cards(project: str | None=None, start_date: str | None=None, end_date: str | None=None) -> list[dict]` — List cards, optionally filtered by project and an overlapping date window.
- `upsert_lookahead_card(data: dict) -> dict` — Insert or update a card; ``data`` must include ``id``.
- `delete_lookahead_card(card_id: str) -> None` — Delete a card.
  <details><summary>full docstring</summary>

  Dependency, link and resource rows are removed by ``ON DELETE CASCADE``, so
  this does not need to clean them up itself.  Deleting a card that does not
  exist is a no-op rather than an error.

  :param card_id: The card's UUID.
  </details>
- `set_card_dependencies(card_id: str, depends_on_ids: list[str]) -> None` — Replace a card's dependency set wholesale.
  <details><summary>full docstring</summary>

  Existing rows are deleted first, so passing an empty or ``None`` list clears
  the dependencies.  A card listing itself is skipped -- a self-dependency is
  meaningless and would make the card permanently unschedulable.  Duplicates
  are absorbed by ``INSERT OR IGNORE``.

  :param card_id: The dependent card's UUID.
  :param depends_on_ids: UUIDs this card should depend on.
  </details>
- `set_card_links(card_id: str, links: list[dict]) -> None` — Replace the user-editable link set on a card. ``links`` is [{type, id}, ...].
  <details><summary>full docstring</summary>

  The ``todo`` link type is auto-managed (one todo per card, created on card
  insert, kept in sync by app endpoints) and is preserved across this call
  even if not present in ``links``.
  </details>
- `get_card_todo_id(card_id: str) -> int | None` — Return the integer todo id linked to this card, or None.
- `get_cards_for_todo(todo_id: int) -> list[str]` — Return card ids linked to a todo (usually zero or one).
- `set_card_todo_link(card_id: str, todo_id: int) -> None` — Replace the card's todo link with a single pointer to ``todo_id``.
- `list_cards_without_todo() -> list[dict]` — Return all cards that have no linked todo.
- `set_card_resources(card_id: str, entries: list[dict]) -> None` — Replace the card's BOM entries. ``entries`` is [{resource_id, quantity, status}].
- `set_card_resource_status(card_id: str, resource_id: int, status: str) -> None` — Set the fulfilment status of one resource assigned to one card.
  <details><summary>full docstring</summary>

  This is the BOM status the board cycles through (e.g. ordered / received).
  A pairing that is not currently assigned updates nothing rather than raising.

  :param card_id: The card's UUID.
  :param resource_id: The assigned resource's integer primary key.
  :param status: One of ``_RESOURCE_STATUSES``.
  :raises ValueError: If ``status`` is not a recognised status.
  </details>
- `list_resources(type_filter: str | None=None) -> list[dict]` — List the global resource catalog, ordered by type then name.
  <details><summary>full docstring</summary>

  :param type_filter: Optional resource type to restrict to; ``None`` returns
      every resource.
  :return: Resource rows as dicts.
  </details>
- `get_resource(resource_id: int) -> dict | None` — Return one resource from the global catalog.
  <details><summary>full docstring</summary>

  :param resource_id: The resource's integer primary key.
  :return: The resource dict, or ``None`` if no such resource exists.
  </details>
- `create_resource(name: str, type_: str, notes: str='') -> dict` — Add a resource to the global catalog.
  <details><summary>full docstring</summary>

  :param name: Display name; surrounding whitespace is stripped.
  :param type_: One of ``_RESOURCE_TYPES``.
  :param notes: Optional free-text note.
  :return: The newly created resource dict.
  :raises ValueError: If ``type_`` is not a recognised resource type.
  </details>
- `update_resource(resource_id: int, updates: dict) -> dict | None` — Patch a resource's ``name``, ``type`` or ``notes``.
  <details><summary>full docstring</summary>

  Keys outside that set are ignored rather than rejected, so a caller may hand
  over a whole request body.  An update containing none of them is a no-op that
  still returns the current row.

  :param resource_id: The resource's integer primary key.
  :param updates: Partial field/value mapping.
  :return: The updated resource dict, or ``None`` if it does not exist.
  :raises ValueError: If ``type`` is present but not a recognised type.
  </details>
- `delete_resource(resource_id: int) -> None` — Remove a resource from the global catalog.
  <details><summary>full docstring</summary>

  Per-card assignments referencing it are removed by ``ON DELETE CASCADE``.

  :param resource_id: The resource's integer primary key.
  </details>
- `list_project_shifts(project_tag: str | None=None) -> list[dict]` — List shift definitions, ordered by project then shift number.
  <details><summary>full docstring</summary>

  :param project_tag: Optional project to restrict to; ``None`` returns the
      shift schedules for every project.
  :return: Shift rows as dicts.
  </details>
- `upsert_project_shift(project_tag: str, shift_num: int, data: dict) -> dict` — Insert or update a single shift row for a project.
- `delete_project_shift(project_tag: str, shift_num: int) -> None` — Delete one shift from a project's schedule.
  <details><summary>full docstring</summary>

  Cards already scheduled against this shift number are left untouched; the
  shift row only defines the label and hours the board renders.

  :param project_tag: The project the shift belongs to.
  :param shift_num: The shift's number within that project.
  </details>
- `get_template(template_id: str) -> dict | None` — Return one template with its task graph attached.
  <details><summary>full docstring</summary>

  Each task in the returned ``tasks`` list carries its own ``depends_on`` local
  ids and ``resource_requirements``.

  :param template_id: The template's UUID.
  :return: The template dict, or ``None`` if no such template exists.
  </details>
- `list_templates(owner: str | None=None) -> list[dict]` — List templates, each with its task graph attached, ordered by name.
  <details><summary>full docstring</summary>

  :param owner: Optional owner to restrict to; ``None`` returns every template.
  :return: Template dicts, hydrated the same way as :func:`get_template`.
  </details>
- `create_template(data: dict) -> dict` — Create a new template. Expects ``id`` set by caller (UUID).
- `update_template(template_id: str, updates: dict) -> dict | None` — Apply partial update. Bumps ``version`` unconditionally.
- `delete_template(template_id: str) -> None` — Delete a template and its task graph.
  <details><summary>full docstring</summary>

  Tasks, dependencies and resource requirements go with it via
  ``ON DELETE CASCADE``.  Cards already instantiated from this template are
  deliberately left in place -- they are concrete scheduled work, and deleting
  the pattern they came from should not erase them.

  :param template_id: The template's UUID.
  </details>
- `instantiate_template(template_id: str, start_date: str, project_tag: str, owner: str='') -> dict | None` — Materialise a template instance.  Returns ``{instance, cards}``.
- `get_instance(instance_id: str) -> dict | None` — Return one template instance with its cohort of cards attached.
  <details><summary>full docstring</summary>

  :param instance_id: The instance's UUID.
  :return: The instance dict including its ``cards``, or ``None`` if no such
      instance exists.
  </details>
- `list_instances(project: str | None=None, status: str | None=None) -> list[dict]` — List template instances, optionally filtered by project and status.
  <details><summary>full docstring</summary>

  :param project: Optional project tag to restrict to.
  :param status: Optional instance status to restrict to (see
      ``_INSTANCE_STATUSES``).
  :return: Instance rows as dicts.
  </details>
- `list_lookahead_cards_for_instance(instance_id: str) -> list[dict]` — List the cards created by one template instantiation, in schedule order.
  <details><summary>full docstring</summary>

  This is the cohort a reschedule moves together: every card stamped with the
  same ``template_instance_id`` shifts by the same delta.

  :param instance_id: The instance's UUID.
  :return: Card dicts, hydrated the same way as :func:`get_lookahead_card`.
  </details>
- `reschedule_instance(instance_id: str, new_start_date: str) -> dict | None` — Shift all cards attached to the instance by (new_start_date - old_start_date).
  <details><summary>full docstring</summary>

  Uses the instance's ``duration_unit`` via its template to honour
  business-days vs calendar-days semantics.  Cards whose
  ``template_instance_id`` has been nulled (detached) are untouched.
  </details>
- `upgrade_instance(instance_id: str) -> dict | None` — Re-apply the current template version to an existing instance.
  <details><summary>full docstring</summary>

  Per parsival#60: the user opted in to this upgrade.  Existing cards keep
  their assignee, status, notes, and any user-added BOM entries; the template-
  derived fields (title, schedule offsets, linked procedure doc, required
  named resources) get refreshed.  Tasks added since the original
  instantiation become new cards.  Tasks that were removed from the template
  leave their existing cards alone — they may already be in flight.
  Dependencies are rebuilt from the current template graph.
  </details>
- `set_instance_status(instance_id: str, status: str) -> dict | None` — Move a template instance to a new lifecycle status.
  <details><summary>full docstring</summary>

  The instance's cards are not touched: cancelling an instance records that the
  cohort is no longer being pursued without deleting the scheduled work.

  :param instance_id: The instance's UUID.
  :param status: One of ``_INSTANCE_STATUSES``.
  :return: The updated instance dict, or ``None`` if it does not exist.
  :raises ValueError: If ``status`` is not a recognised instance status.
  </details>
- `delete_instance(instance_id: str) -> None` — Delete an instance and its still-attached cards.  Detached cards stay.
- `detach_card(card_id: str) -> dict | None` — Remove the card from its template instance without deleting it.
- `maybe_autocomplete_instance(instance_id: str) -> None` — Flip instance to ``complete`` if every attached card is done.
- `list_card_suggestions(card_id: str, include_decided: bool=False) -> list[dict]` — Return suggestions for a card.  Pending only by default.
- `add_card_suggestion(card_id: str, link_type: str, target_id: str, reason: str='') -> dict | None` — Insert a new pending suggestion.  Deduped on (card, type, target).
- `decide_card_suggestion(suggestion_id: int, decision: str) -> dict | None` — Accept or reject a suggestion.  Accepted ones also become card_links.
- `slack_unseen_message_ts(team: str, channel_id: str, ts_list: list[str]) -> set[str]` — Return the timestamps in ``ts_list`` not yet recorded as seen.
  <details><summary>full docstring</summary>

  Scoped to the given ``(team, channel_id)``.

  Used by ``connector_slack._fetch_for_token`` to keep only newly-surfaced
  messages in each scan.  The connector calls this to filter the raw
  channel/DM/mention message list before building a ``RawItem``; if the
  filtered list is empty no item is emitted.

  :param team: Slack workspace name, or ``""`` for the legacy bot path.
  :param channel_id: Slack channel ID (``C...``, ``D...``, or ``G...``).
  :param ts_list: Message timestamps to test (native Slack ``ts`` strings).
  :return: Set of ts strings that are not yet in ``slack_seen_messages``.
  </details>
- `slack_mark_messages_seen(team: str, channel_id: str, ts_list: list[str]) -> None` — Record ``(team, channel_id, ts)`` tuples as already surfaced.
  <details><summary>full docstring</summary>

  Called by the Slack connector right after it emits a ``RawItem`` built
  from ``ts_list`` so subsequent scans can skip those messages.  Uses
  ``INSERT OR IGNORE`` to stay idempotent — re-calling with the same
  timestamps is a no-op.
  </details>
- `candidate_items_for_card(project: str, start_date: str, end_date: str, limit: int=40) -> list[dict]` — Return same-project items whose timestamp sits near the card window.
  <details><summary>full docstring</summary>

  Used as the shortlist the LLM ranks against the card.
  </details>

### `api.embedder`
*embedder.py — Sentence-embedding based project classifier.*

- `embed(text: str) -> list` — Embed text, normalise to unit length, return as a plain Python list.
- `update_project(project_name: str, item_id: str, vector: list, category: str, hierarchy: str, source: str, priority: str, old_project: str=None, old_category: str=None) -> None` — Upsert an item vector into project ``project_name`` and recompute centroids.
- `score_item(vector: list, min_count: int=3) -> list` — Score a vector against all stored project centroids. Returns top 5 matches.
- `remove_item(item_id: str, project_name: str) -> None` — Remove an item from a project and recompute all affected centroids.
- `get_item_vector(item_id: str)` — Retrieve the stored embedding vector for a specific item_id across all projects.
  <details><summary>full docstring</summary>

  Returns None if the item has not been embedded.

  Note: callers processing many item_ids in a batch should prefer
  ``get_all_item_vectors()`` instead — this function walks every stored
  project's items list on each call.
  </details>
- `get_all_item_vectors() -> dict` — Return a dict mapping ``item_id`` to its stored embedding vector.
  <details><summary>full docstring</summary>

  Covers every item across every project, built in a single pass over the
  embeddings table. If an item is stored under multiple projects the first
  match wins
  (vectors are invariant per item, so this is safe).

  Callers that need attention scores for a full list of items should call
  this once and look up each item from the returned dict, rather than
  calling ``get_item_vector`` per item — the latter walks the whole
  embeddings table on every call.
  </details>
- `get_project_stats() -> dict` — Return ``{project_name: {total_items, subdivisions}}`` for all stored projects.

### `api.graph`
*graph.py — Knowledge graph layer for Squire.*

- `index_item(analysis) -> None` — Ingest a saved Analysis object into the knowledge graph.
  <details><summary>full docstring</summary>

  Creates or updates nodes for the item, its author, project, and
  conversation, then upserts directed edges connecting them.  Safe to
  call multiple times for the same item (all operations are idempotent).

  :param analysis: A saved ``Analysis`` dataclass instance.
  </details>
- `index_item_situation(item_id: str, situation_id: str) -> None` — Add a situation edge for an item that has been grouped after initial indexing.
  <details><summary>full docstring</summary>

  :param item_id: The item's ID.
  :param situation_id: The situation's ID.
  </details>
- `get_context(item, max_n: int=5) -> list[dict]` — Retrieve the most relevant prior items from the graph for a given item.
  <details><summary>full docstring</summary>

  Looks up the item's conversation, author, and project nodes, collects
  all connected items, scores each by ``base_weight × recency_decay``, and
  returns the top ``max_n`` as full item dicts from the items table.

  The result is empty if the item has no matching nodes in the graph yet
  (e.g. first scan run).

  :param item: A ``RawItem`` or an ``Analysis`` — any object with
               ``item_id`` and ``metadata`` attributes, or an ``item_id``
               str plus ``source``, ``author``, ``conversation_id``,
               ``conversation_topic``, and ``project_tag`` attributes.
  :param max_n: Maximum number of context items to return.
  :return: List of scored item dicts sorted by descending context score.
           Each dict is a plain items-table row with an added
           ``"context_score"`` and ``"context_edge"`` key.
  </details>
- `format_context(context_items: list[dict]) -> str` — Render a list of context items as a human-readable prompt section.
  <details><summary>full docstring</summary>

  Groups items by ``context_edge`` so the LLM can see which relationship
  each item arrived through.

  :param context_items: Output of ``get_context()``.
  :return: Multi-line string ready to embed in the analysis prompt, or
           empty string if ``context_items`` is empty.
  </details>

### `api.lancellmot_client`
*HTTP client for lancellmot's workspace + documents API (parsival#43).*

- `LancellmotUnavailable` — Raised on network error, timeout, or non-2xx response from lancellmot.
- `list_projects() -> list[dict]` — Return all lancellmot projects. Raises LancellmotUnavailable on failure.
- `list_documents(project_id: str, limit: int=5) -> list[dict]` — Return the first ``limit`` documents for a lancellmot project.
  <details><summary>full docstring</summary>

  Raises LancellmotUnavailable on network error, timeout, or non-2xx response.
  </details>

### `api.llm`
*llm.py — Unified LLM provider abstraction.*

- `generate(prompt: str, *, format: str | None='json', temperature: float=0.1, num_predict: int=768, num_ctx: int=8192, timeout: int=90, priority: str='short') -> str` — Send a prompt to the configured LLM provider and return the response text.
  <details><summary>full docstring</summary>

  :param prompt: The full prompt string.
  :param format: Response format hint ("json" or None). Used by Ollama;
                 for Claude, the system prompt requests JSON output.
  :param temperature: Sampling temperature.
  :param num_predict: Max tokens to generate.
  :param num_ctx: Context window size (Ollama only).
  :param timeout: Request timeout in seconds.
  :param priority: merLLM priority bucket (``chat``, ``embeddings``,
      ``short``, ``feedback``, ``background``). Forwarded as the
      ``X-Priority`` header on Ollama calls. Defaults to ``short`` so
      any new call site that forgets to choose lands in a safe middle
      bucket instead of starving chat or jumping ahead of bulk work.
      Note: ``embeddings`` is auto-routed by merLLM at the
      ``/api/embeddings`` endpoint, so generate/chat callers should
      not pick it (merLLM#38).
  :return: Raw response text from the LLM.
  :raises requests.HTTPError: On non-2xx response.
  </details>

### `api.models`
*models.py — Core data models for the Squire analysis pipeline.*

- `RawItem` — A normalised, source-agnostic item ready for AI analysis.
  <details><summary>full docstring</summary>

  :ivar source: Originating system identifier, e.g. ``"outlook"``, ``"slack"``.
  :ivar item_id: Stable unique ID for this item used for deduplication.
  :ivar title: Short human-readable title or subject line.
  :ivar body: Full text content, truncated to 3 000 characters by convention.
  :ivar url: Deep link back to the source item, or empty string if unavailable.
  :ivar author: Display name and/or email of the sender or creator.
  :ivar timestamp: ISO 8601 timestamp of when the item was created or received.
  :ivar metadata: Arbitrary source-specific key/value pairs (channel, status,
                  conversation_id, direction, etc.).
  </details>
  - `source: str` — (undocumented)
  - `item_id: str` — (undocumented)
  - `title: str` — (undocumented)
  - `body: str` — (undocumented)
  - `url: str` — (undocumented)
  - `author: str` — (undocumented)
  - `timestamp: str` — (undocumented)
  - `metadata: dict = field(default_factory=dict)` — (undocumented)
- `ActionItem` — A single concrete action extracted from an analysed item.
  <details><summary>full docstring</summary>

  :ivar description: Human-readable description of the required action.
  :ivar deadline: ISO 8601 date string if a due date was identified, else ``None``.
  :ivar owner: Who is responsible — ``"me"`` or a named person.
  </details>
  - `description: str` — (undocumented)
  - `deadline: str | None` — (undocumented)
  - `owner: str` — (undocumented)
- `Analysis` — The structured result of running a ``RawItem`` through the LLM pipeline.
  <details><summary>full docstring</summary>

  :ivar item_id: ID of the originating ``RawItem``.
  :ivar source: Originating system identifier.
  :ivar title: Title carried forward from the ``RawItem``.
  :ivar author: Author carried forward from the ``RawItem``.
  :ivar timestamp: Timestamp carried forward from the ``RawItem``.
  :ivar url: Deep link carried forward from the ``RawItem``.
  :ivar has_action: ``True`` if at least one action item was identified.
  :ivar priority: Urgency level — one of ``"high"``, ``"medium"``, ``"low"``.
  :ivar category: Item classification — one of ``"task"``, ``"approval"``,
                  ``"fyi"``, ``"noise"``.
  :ivar task_type: Sub-type for task items — ``"review"``, ``"reply"``, or
                   ``None`` for general tasks.
  :ivar action_items: List of concrete actions extracted by the LLM.
  :ivar summary: One-sentence summary of the item and required action.
  :ivar urgency_reason: Brief explanation of the assigned priority, or ``None``.
  :ivar hierarchy: Relevance tier — ``"user"``, ``"project"``, ``"topic"``,
                   or ``"general"``.  Defaults to ``"general"``.
  :ivar is_passdown: ``True`` when the item is a shift handoff/passdown note.
  :ivar project_tag: JSON-serialized list of project names this item belongs
                     to, or ``None`` if untagged.  A single string is also
                     accepted for backward compat.
  :ivar direction: ``"received"`` (default) or ``"sent"`` — whether the item
                   was received by or sent by the user.
  :ivar conversation_id: Stable thread/conversation identifier from the source
                         system (e.g. Outlook ConversationID).  ``None`` if
                         unavailable.
  :ivar conversation_topic: Cleaned subject / thread title without Re:/Fw:
                             prefixes.  ``None`` if unavailable.
  :ivar goals: Project goals or objectives extracted from the content.
  :ivar key_dates: Deadlines or time references extracted from the content.
  :ivar body_preview: Up to 2000 characters of the item body for re-analysis
                      and keyword learning.
  :ivar to_field: Raw ``To`` header value carried forward from item metadata.
  :ivar cc_field: Raw ``CC`` header value carried forward from item metadata.
  :ivar is_replied: ``True`` when the user has already replied to this item.
  :ivar replied_at: ISO 8601 timestamp of the user's reply, or ``None``.
  :ivar information_items: Factual observations and completed-action notes
                           extracted by the LLM that are not tasks for the user.
  </details>
  - `item_id: str` — (undocumented)
  - `source: str` — (undocumented)
  - `title: str` — (undocumented)
  - `author: str` — (undocumented)
  - `timestamp: str` — (undocumented)
  - `url: str` — (undocumented)
  - `has_action: bool` — (undocumented)
  - `priority: str` — (undocumented)
  - `category: str` — (undocumented)
  - `action_items: list[ActionItem]` — (undocumented)
  - `summary: str` — (undocumented)
  - `urgency_reason: str | None` — (undocumented)
  - `task_type: str | None = None` — (undocumented)
  - `hierarchy: str = 'general'` — (undocumented)
  - `is_passdown: bool = False` — (undocumented)
  - `project_tag: str | None = None` — (undocumented)
  - `direction: str = 'received'` — (undocumented)
  - `conversation_id: str | None = None` — (undocumented)
  - `conversation_topic: str | None = None` — (undocumented)
  - `goals: list[str] = field(default_factory=list)` — (undocumented)
  - `key_dates: list[dict] = field(default_factory=list)` — (undocumented)
  - `body_preview: str = ''` — (undocumented)
  - `to_field: str = ''` — (undocumented)
  - `cc_field: str = ''` — (undocumented)
  - `is_replied: bool = False` — (undocumented)
  - `replied_at: str | None = None` — (undocumented)
  - `information_items: list[dict] = field(default_factory=list)` — (undocumented)
- `Situation` — A cross-source grouping of related Analysis items.
  <details><summary>full docstring</summary>

  :ivar situation_id: Stable UUID for this situation.
  :ivar title: LLM-generated short title.
  :ivar summary: LLM-generated cross-source narrative (2-3 sentences).
  :ivar status: Operational status — ``"blocked"``, ``"in_progress"``,
                ``"waiting"``, ``"needs_decision"``, or ``"informational"``.
  :ivar item_ids: Ordered list of contributing analysis item_ids.
  :ivar sources: Unique source types present in this situation.
  :ivar project_tag: Shared project tag if all items agree, else ``None``.
  :ivar score: Composite urgency score.
  :ivar priority: Derived from highest-priority contributing item.
  :ivar open_actions: Deduplicated action items across all contributing items.
  :ivar references: Union of all extracted reference strings from members.
  :ivar key_context: LLM-extracted essential background sentence, or ``None``.
  :ivar last_updated: ISO timestamp of most recently updated contributing item.
  :ivar created_at: ISO timestamp when this situation was first formed.
  :ivar score_updated_at: ISO timestamp of last score recomputation.
  </details>
  - `situation_id: str` — (undocumented)
  - `title: str` — (undocumented)
  - `summary: str` — (undocumented)
  - `status: str` — (undocumented)
  - `item_ids: list` — (undocumented)
  - `sources: list` — (undocumented)
  - `project_tag: str | None` — (undocumented)
  - `score: float` — (undocumented)
  - `priority: str` — (undocumented)
  - `open_actions: list` — (undocumented)
  - `references: list` — (undocumented)
  - `key_context: str | None` — (undocumented)
  - `last_updated: str` — (undocumented)
  - `created_at: str` — (undocumented)
  - `score_updated_at: str` — (undocumented)

### `api.noise_filter`
*noise_filter.py — Pre-scan noise filter evaluation.*

- `should_filter(item: RawItem, rules: list[dict]) -> tuple[bool, str | None]` — Return whether any pre-scan noise rule matches *item*.
  <details><summary>full docstring</summary>

  ``(True, matched_rule_type)`` when a rule matches, else ``(False, None)``.
  </details>
- `validate_rule(rule: dict) -> str | None` — Validate a single filter rule.  Returns an error string or None if valid.

### `api.orchestrator`
*orchestrator.py — Scan, reanalyze, and ingest orchestration for Squire.*

- `init(scan_state: dict, save_analysis_fn, spawn_situation_fn, generate_briefing_fn=None) -> None` — Inject shared state and callables from app.py.
  <details><summary>full docstring</summary>

  Must be called once at startup before any scan or ingest endpoints
  are invoked.
  </details>
- `run_scan(sources: list[str]) -> None` — Fetch items from one or more connectors and run LLM analysis on each.
  <details><summary>full docstring</summary>

  Iterates ``sources`` in order, calling the matching connector's ``fetch()``
  method.  Each item is then passed to ``agent.analyze``; concurrency
  against the LLM is owned entirely by merLLM (see the module docstring
  for the squire#33 rationale).  Saves every result via ``_save_analysis``
  and spawns a situation-formation task per item.  A scan log entry is
  written regardless of success or cancellation.

  Progress is reflected in the shared ``scan_state`` dict, which the
  frontend polls via ``GET /scan/status``.

  :param sources: List of connector names to fetch from, e.g.
                  ``["slack", "github", "jira"]``.
  :type sources: list[str]
  </details>
- `run_reanalyze() -> None` — Re-run LLM analysis on all stored items using the current config.
  <details><summary>full docstring</summary>

  Reconstructs a ``RawItem`` from each stored analysis record (using
  ``body_preview``, ``to_field``, ``cc_field``, and existing ``project_tag``
  as a manual-tag hint), passes it through ``analyze()``, then calls
  ``_save_analysis(reanalyze=True)`` to replace stale todos and intel.
  Situation formation is re-triggered for each item after save.
  Reuses ``scan_state`` for progress reporting.
  </details>
- `claim_ingest_items(item_ids: list[str]) -> set[str]` — Atomically mark ``item_ids`` as in-flight and return the new ones.
  <details><summary>full docstring</summary>

  "New" means not already in the DB and not currently being processed.

  Used by ``POST /ingest`` to deduplicate against both persisted items
  *and* items queued by a previous call whose background task has not yet
  finished (parsival#58). Caller is expected to pass the claimed ids to
  ``process_ingest_items`` so they eventually leave the set via
  ``release_ingest_item``.

  :param item_ids: Candidate item IDs from the incoming ingest batch.
  :return: Subset of ``item_ids`` newly claimed for processing.
  </details>
- `release_ingest_item(item_id: str) -> None` — Remove ``item_id`` from the in-flight set after processing settles.
- `process_ingest_items(raw: list[RawItem]) -> None` — Analyse a pre-filtered list of new raw items from the ingest endpoint.
  <details><summary>full docstring</summary>

  Called as a FastAPI background task.  Respects ``scan_state["cancelled"]``
  and tracks in-flight count via ``scan_state["ingest_pending"]``.

  Items are fanned out over a bounded ``ThreadPoolExecutor`` so merLLM's
  scheduler sees multiple parsival jobs at once and can dispatch them
  across GPU slots (parsival#75). Concurrency is capped by
  ``INGEST_CONCURRENCY`` (default 4) — enough to keep both GPUs busy
  with a little queue headroom, without holding hundreds of HTTP calls
  open at once. GPU-level concurrency remains owned by merLLM.

  :param raw: List of deduplicated ``RawItem`` objects to analyse.
  :type raw: list[RawItem]
  </details>
- `scheduler_update(schedule_dict: dict) -> None` — Apply a new scan schedule.
  <details><summary>full docstring</summary>

  ``schedule_dict`` maps source names to interval minutes (0 = disabled).
  Existing timers are cancelled; new ones are armed for non-zero intervals.

  Example: ``{"slack": 30, "github": 60, "jira": 0, "outlook": 0, "teams": 0}``
  </details>
- `get_schedule_status() -> dict` — Return per-source schedule status for GET /scan/status.

### `api.seeder`
*seeder.py — Seed state machine for bootstrapping project config.*

- `init(scan_state: dict, run_scan_fn, run_reanalyze_fn, maybe_form_situation_fn) -> None` — Inject shared state and callables from app.py.
  <details><summary>full docstring</summary>

  Must be called once at startup before any seed endpoints are invoked.
  </details>
- `start(context: str) -> dict` — Start the seed state machine.  Always succeeds immediately.
  <details><summary>full docstring</summary>

  Returns the current _seed_job state.
  </details>
- `update_context(context: str) -> None` — Update the user-provided context string while waiting for ingest.
- `status() -> dict` — Return the current state of the background seed job.
- `cancel() -> None` — Signal the seed job to stop after the current step.
- `apply(body: dict, background_tasks) -> dict` — Apply the seed editor's confirmed projects and topics to settings.
  <details><summary>full docstring</summary>

  Steps:
  1. Merges new projects into config.PROJECTS (skipping duplicates).
  2. Merges new topics into config.FOCUS_TOPICS.
  3. Persists updated settings and calls config.apply_overrides.
  4. Optionally retags existing analyses against newly added projects.
  5. Background: embeds tagged items and sweeps situation formation.
  6. Starts _run_reanalyze in a daemon thread and monitors it.

  :param body: Dict with keys ``projects``, ``topics``, ``retag``.
  :param background_tasks: FastAPI BackgroundTasks runner.
  :return: ``{"ok": True, "projects_added": N, "projects_merged": M, "topics_added": T, "items_retagged": K}``
  </details>
- `run_scan(scan_state: dict) -> dict` — Transition the seed state machine to scanning and start a full connector scan.
  <details><summary>full docstring</summary>

  :param scan_state: The shared scan_state dict from app.py.
  :return: ``{"ok": True}``
  :raises fastapi.HTTPException 409: If a scan is already running.
  </details>
- `skip_scan() -> dict` — Transition the seed state machine from scan_prompt to done without scanning.
  <details><summary>full docstring</summary>

  :return: ``{"ok": True}``
  </details>

### `api.signatures`
*signatures.py — Email-signature parser for the contacts table (squire#31).*

- `SignatureFields` — Result of a single signature parse, with per-field confidence (0..1).
  <details><summary>full docstring</summary>

  Empty strings mean "no signal at all" — distinct from a low-confidence
  guess.  ``apply_to_contact`` will skip writing any field whose confidence
  is below its threshold, so a guess that comes back as
  ``("Acme", 0.4)`` is still recorded for diagnostic purposes but will not
  actually update the row.
  </details>
  - `name: str = ''` — (undocumented)
  - `name_conf: float = 0.0` — (undocumented)
  - `phone: str = ''` — (undocumented)
  - `phone_conf: float = 0.0` — (undocumented)
  - `title: str = ''` — (undocumented)
  - `title_conf: float = 0.0` — (undocumented)
  - `employer: str = ''` — (undocumented)
  - `employer_conf: float = 0.0` — (undocumented)
  - `employer_address: str = ''` — (undocumented)
  - `address_conf: float = 0.0` — (undocumented)
  - `is_empty(self) -> bool` — True if the parser found nothing usable at all.
  - `confidence_map(self) -> dict[str, float]` — Confidence dict suitable for storage in ``signature_confidence``.
    <details><summary>full docstring</summary>

    Only includes fields the parser actually populated; an empty value
    with a 0.0 score is omitted so the persisted JSON stays small.
    </details>
- `extract_signature_block(body: str) -> str` — Return the trailing chunk of *body* that looks like a signature.
  <details><summary>full docstring</summary>

  The walk is bottom-up so a quoted reply tail or a disclaimer that lives
  *above* the actual signature still gets chopped off — we only return what
  sits between the last quote/disclaimer marker and the end of the message.

  The result is at most ``MAX_TAIL_LINES`` lines, leading/trailing whitespace
  stripped.  Returns an empty string if there is nothing left after pruning.
  </details>
- `parse_signature(block: str, sender_domain: str | None=None) -> SignatureFields` — Heuristic field extraction from a signature block.
  <details><summary>full docstring</summary>

  The strategy is intentionally cheap: regex for the high-signal field
  (phone), structural guesses for name/title/employer, and a multi-line
  pattern for the address tail.  Each field gets a confidence score that
  callers can threshold against.

  ``sender_domain`` is the domain part of the sender email — when supplied
  it nudges the employer guess (e.g. ``acme.com`` → ``Acme``) when the body
  has no obvious "Company Name" line.
  </details>
- `apply_to_contact(contact_id: int, fields: SignatureFields, threshold: float=DEFAULT_CONFIDENCE_THRESHOLD) -> dict` — Merge a parsed signature into the contacts row.
  <details><summary>full docstring</summary>

  Rules
  -----
  * Never overwrite a field that appears in ``manually_edited_fields``.
  * Never overwrite the ``name`` field — header scraping owns names, the
    signature parser only fills it in when the existing name is empty.
    (Confirmed approach in the squire#31 design discussion.)
  * Skip any field whose confidence is below ``threshold``.
  * Stamp ``<field>_source = 'signature'`` on every field actually written.
  * Always refresh ``signature_confidence`` so the UI sees the latest
    per-field scores even when nothing was written this round.

  Returns a small dict describing what changed (used by the rebuild
  endpoint and tests).
  </details>
- `parse_item_body(item: dict, threshold: float=DEFAULT_CONFIDENCE_THRESHOLD) -> dict` — Glue called from the live ingestion path.
  <details><summary>full docstring</summary>

  Resolves the *author* of an item to a contact (header scraping must have
  run first), runs the parser on its body, and applies the result.  This
  is intentionally a no-op when:

  * the item has no body
  * the author email is unknown
  * the author email does not yet correspond to a contact
    (``scrape_item_headers`` should have created one moments earlier — if
    it did not, there is no row to enrich and we silently skip)

  All exceptions are caught and logged.  Signature parsing must never
  break the analysis save path.

  Returns ``{"applied": bool, "fields": [...]}`` for tests / API callers.
  </details>
- `reparse_all_items(threshold: float=DEFAULT_CONFIDENCE_THRESHOLD) -> dict` — Walk every item in the corpus and re-run signature parsing.
  <details><summary>full docstring</summary>

  Mirrors ``contacts.rebuild_from_items`` for the body-parsing pass.
  Idempotent: re-running on the same corpus will not flip provenance away
  from manual or change a field whose confidence is unchanged.
  </details>

### `api.situation_manager`
*situation_manager.py — Situation formation, scoring, and response helpers.*

- `init(scan_state: dict) -> None` — Inject shared scan state from app.py and start the score-decay daemon.
  <details><summary>full docstring</summary>

  Must be called once at startup before any route handlers execute.
  </details>
- `now_iso() -> str` — Return the current UTC time as an ISO 8601 string.
- `split_situation(sit_id: str, item_ids_to_split: list[str], new_title: str | None=None) -> str` — Move a subset of items out of ``sit_id`` into a brand-new situation.
  <details><summary>full docstring</summary>

  Both situations are rescored lightweight (no LLM).  The caller gets back
  the new situation's UUID so the UI can focus it.

  :raises ValueError: if ``item_ids_to_split`` is empty, contains ids not in
                      the source situation, or would leave either situation
                      empty.
  </details>
- `merge_situations(target_id: str, source_id: str) -> None` — Merge ``source_id`` into ``target_id``.
  <details><summary>full docstring</summary>

  All source items are relinked to the target, the target is rescored
  lightweight, and the source is dismissed with reason ``merged_into:<id>``
  so history is preserved.

  :raises ValueError: if either situation is missing or target == source.
  </details>
<!-- END: AUTO-GENERATED PUBLIC API -->
