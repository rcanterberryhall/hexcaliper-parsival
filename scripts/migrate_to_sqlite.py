"""
migrate_to_sqlite.py — One-time migration from TinyDB page.db to SQLite.

Reads the existing TinyDB JSON file (data/page.db) and imports every record
into the new SQLite schema defined in api/db.py.  Safe to re-run: records are
upserted so existing data is not duplicated.

Usage (run from the repo root):

    python scripts/migrate_to_sqlite.py
"""
import json
import os
import sys

_REPO_ROOT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SOURCE_PATH = os.path.join(_REPO_ROOT, "data", "page.db")
DEST_PATH   = os.path.join(_REPO_ROOT, "data", "squire.db")

# Set DB_PATH before importing api modules so config.py picks it up at load time
os.makedirs(os.path.join(_REPO_ROOT, "data"), exist_ok=True)
os.environ["DB_PATH"] = DEST_PATH
sys.path.insert(0, os.path.join(_REPO_ROOT, "api"))

import config  # noqa: E402
config.DB_PATH = DEST_PATH
import db      # noqa: E402


def migrate(source_path: str = SOURCE_PATH, dest_path: str = DEST_PATH) -> None:
    print(f"Source: {source_path}")
    print(f"Dest:   {dest_path}")

    if not os.path.exists(source_path):
        print(f"ERROR: source file not found: {source_path}")
        sys.exit(1)

    with open(source_path) as f:
        tdb = json.load(f)

    # ── settings ───────────────────────────────────────────────────────────────
    settings_table = tdb.get("settings", {})
    if settings_table:
        row = list(settings_table.values())[0]
        db.save_settings(row)
        print(f"  settings: migrated 1 row")

    # ── analyses → items ───────────────────────────────────────────────────────
    analyses = list(tdb.get("analyses", {}).values())
    n = 0
    for rec in analyses:
        # Map old category names to new ones
        cat = rec.get("category") or "fyi"
        task_type = None
        if cat == "reply_needed":
            cat, task_type = "task", "reply"
        elif cat == "review":
            cat, task_type = "task", "review"
        elif cat == "deadline":
            cat, task_type = "task", None
        elif cat == "approval":
            # Heuristic: if body has past-tense approval language keep as approval,
            # else reclassify to task/review.  Safe default: keep as approval.
            pass

        data = {
            "item_id":          rec.get("item_id", ""),
            "source":           rec.get("source", "outlook"),
            "direction":        rec.get("direction", "received"),
            "title":            rec.get("title", ""),
            "author":           rec.get("author", ""),
            "timestamp":        rec.get("timestamp", ""),
            "url":              rec.get("url", ""),
            "has_action":       1 if rec.get("has_action") else 0,
            "priority":         rec.get("priority", "low"),
            "category":         cat,
            "task_type":        task_type,
            "summary":          rec.get("summary", ""),
            "user_summary":     rec.get("user_summary"),
            "urgency":          rec.get("urgency"),
            "action_items":     rec.get("action_items", "[]"),
            "hierarchy":        rec.get("hierarchy", "general"),
            "is_passdown":      1 if rec.get("is_passdown") else 0,
            "project_tag":      rec.get("project_tag"),
            "conversation_id":  rec.get("conversation_id"),
            "conversation_topic": rec.get("conversation_topic"),
            "goals":            rec.get("goals", "[]"),
            "key_dates":        rec.get("key_dates", "[]"),
            "information_items": rec.get("information_items", "[]"),
            "body_preview":     rec.get("body_preview", ""),
            "to_field":         rec.get("to_field", ""),
            "cc_field":         rec.get("cc_field", ""),
            "is_replied":       1 if rec.get("is_replied") else 0,
            "replied_at":       rec.get("replied_at"),
            "processed_at":     rec.get("processed_at"),
            "situation_id":     rec.get("situation_id"),
            "references":       rec.get("references", "[]"),
        }
        if not data["item_id"]:
            continue
        with db.lock:
            db.upsert_item(data)
        n += 1
    print(f"  analyses → items: migrated {n} rows")

    # ── todos ──────────────────────────────────────────────────────────────────
    todos = list(tdb.get("todos", {}).values())
    n = 0
    for rec in todos:
        done   = rec.get("done", False)
        status = rec.get("status", "done" if done else "open")
        data = {
            "item_id":          rec.get("item_id"),
            "source":           rec.get("source", ""),
            "title":            rec.get("title", ""),
            "url":              rec.get("url", ""),
            "description":      rec.get("description", ""),
            "user_edited_text": rec.get("user_edited_text"),
            "deadline":         rec.get("deadline"),
            "owner":            rec.get("owner", ""),
            "priority":         rec.get("priority", "medium"),
            "done":             1 if done else 0,
            "status":           status,
            "assigned_to":      rec.get("assigned_to"),
            "is_manual":        1 if rec.get("is_manual") else 0,
            "project_tag":      rec.get("project_tag"),
            "created_at":       rec.get("created_at"),
        }
        with db.lock:
            db.insert_todo(data)
        n += 1
    print(f"  todos: migrated {n} rows")

    # ── intel ──────────────────────────────────────────────────────────────────
    intel = list(tdb.get("intel", {}).values())
    n = 0
    for rec in intel:
        data = {
            "item_id":     rec.get("item_id"),
            "source":      rec.get("source", ""),
            "title":       rec.get("title", ""),
            "url":         rec.get("url", ""),
            "fact":        rec.get("fact", ""),
            "relevance":   rec.get("relevance", ""),
            "project_tag": rec.get("project_tag"),
            "priority":    rec.get("priority", "medium"),
            "timestamp":   rec.get("timestamp"),
            "dismissed":   1 if rec.get("dismissed") else 0,
            "created_at":  rec.get("created_at"),
        }
        with db.lock:
            db.insert_intel(data)
        n += 1
    print(f"  intel: migrated {n} rows")

    # ── situations ─────────────────────────────────────────────────────────────
    situations = list(tdb.get("situations", {}).values())
    n = 0
    for rec in situations:
        data = {
            "situation_id":     rec.get("situation_id", ""),
            "title":            rec.get("title", ""),
            "summary":          rec.get("summary", ""),
            "status":           rec.get("status", "in_progress"),
            "item_ids":         json.dumps(rec.get("item_ids", [])),
            "sources":          json.dumps(rec.get("sources", [])),
            "project_tag":      rec.get("project_tag"),
            "score":            rec.get("score", 0.0),
            "priority":         rec.get("priority", "medium"),
            "open_actions":     json.dumps(rec.get("open_actions", [])),
            "references":       json.dumps(rec.get("references", [])),
            "key_context":      rec.get("key_context"),
            "last_updated":     rec.get("last_updated"),
            "created_at":       rec.get("created_at"),
            "score_updated_at": rec.get("score_updated_at"),
            "dismissed":        1 if rec.get("dismissed") else 0,
        }
        if not data["situation_id"]:
            continue
        with db.lock:
            db.insert_situation(data)
        n += 1
    print(f"  situations: migrated {n} rows")

    # ── scan_logs ──────────────────────────────────────────────────────────────
    scan_logs = list(tdb.get("scan_logs", {}).values())
    n = 0
    for rec in scan_logs:
        data = {
            "started_at":    rec.get("started_at"),
            "finished_at":   rec.get("finished_at"),
            "sources":       rec.get("sources", ""),
            "items_scanned": rec.get("items_scanned", 0),
            "actions_found": rec.get("actions_found", 0),
            "status":        rec.get("status", "completed"),
        }
        with db.lock:
            db.insert_scan_log(data)
        n += 1
    print(f"  scan_logs: migrated {n} rows")

    # ── embeddings ─────────────────────────────────────────────────────────────
    embeddings = list(tdb.get("embeddings", {}).values())
    n = 0
    for rec in embeddings:
        project = rec.get("project", "")
        if not project:
            continue
        items    = rec.get("items", [])
        centroids = rec.get("centroids", {})
        counts    = rec.get("centroid_counts", {})
        with db.lock:
            db.upsert_embedding(project, items, centroids, counts)
        n += 1
    print(f"  embeddings: migrated {n} rows")

    print("\nMigration complete.")
    print(f"SQLite database written to: {dest_path}")
    print("\nNext steps:")
    print("  1. Set DB_PATH in your .env / docker-compose to point to the new .db file")
    print("  2. Restart the Squire container")
    print("  3. Run a re-analyze to populate graph edges for existing items")


if __name__ == "__main__":
    migrate()
