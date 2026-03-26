"""
embedder.py — Sentence-embedding based project classifier.

Loads all-MiniLM-L6-v2 once at module import and provides helpers for
storing per-project item vectors, computing subdivision centroids, and
scoring new items against stored centroids.

All embedding state is stored in the ``embeddings`` table via ``db.py``.

If ``sentence-transformers`` is not installed, ``_AVAILABLE`` is ``False``
and all functions fail silently, leaving the keyword system as sole fallback.
"""
import threading
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:
    np = None

try:
    from sentence_transformers import SentenceTransformer
    _model = SentenceTransformer("all-MiniLM-L6-v2")
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

import db

_lock = threading.Lock()


class _EmbeddingsTbl:
    """Minimal shim used by tests that call embedder._get_tbl() methods."""

    def truncate(self):
        db.conn().execute("DELETE FROM embeddings")

    def get(self, pred=None):
        """Query embeddings table by TinyDB-style predicate (project == value)."""
        import json as _json
        # Extract (field, value) from TinyDB QueryInstance
        h = getattr(pred, "_hash", None)
        if h and len(h) == 3 and h[0] == "==":
            field_path, val = h[1], h[2]
            if isinstance(field_path, (tuple, list)) and len(field_path) == 1:
                field = field_path[0]
                row = db.conn().execute(
                    f"SELECT * FROM embeddings WHERE \"{field}\" = ?", (val,)
                ).fetchone()
                if not row:
                    return None
                d = dict(row)
                for col in ("items", "centroids", "centroid_counts"):
                    if col in d and isinstance(d[col], str):
                        try:
                            d[col] = _json.loads(d[col])
                        except Exception:
                            pass
                return d
        return None


def _get_tbl() -> _EmbeddingsTbl:
    """Return a shim object with a truncate() method (test compatibility)."""
    return _EmbeddingsTbl()


# ── Public API ─────────────────────────────────────────────────────────────────

def embed(text: str) -> list:
    """Embed text, normalise to unit length, return as a plain Python list."""
    if not _AVAILABLE:
        return []
    vec = _model.encode(text[:2048], normalize_embeddings=True)
    return vec.tolist()


def _recompute_centroid(items: list, category: str):
    vectors = [item["vector"] for item in items if item["category"] == category]
    if not vectors:
        return None
    arr      = np.array(vectors)
    centroid = np.mean(arr, axis=0)
    norm     = np.linalg.norm(centroid)
    if norm:
        centroid = centroid / norm
    return centroid.tolist()


def update_project(
    project_name: str,
    item_id: str,
    vector: list,
    category: str,
    hierarchy: str,
    source: str,
    priority: str,
    old_project: str = None,
    old_category: str = None,
) -> None:
    """Upsert an item vector into project ``project_name`` and recompute centroids."""
    if not _AVAILABLE or not vector:
        return
    with _lock:
        # ── 1. Remove from old project if moving cross-project ───────────────
        if old_project and old_project != project_name:
            old_rec = db.get_embedding(old_project)
            if old_rec:
                removed_cats = {i["category"] for i in old_rec.get("items", []) if i["item_id"] == item_id}
                old_items    = [i for i in old_rec.get("items", []) if i["item_id"] != item_id]
                centroids    = dict(old_rec.get("centroids", {}))
                counts       = dict(old_rec.get("centroid_counts", {}))
                for cat in removed_cats:
                    c = _recompute_centroid(old_items, cat)
                    if c:
                        centroids[cat] = c
                        counts[cat]    = sum(1 for i in old_items if i["category"] == cat)
                    else:
                        centroids.pop(cat, None)
                        counts.pop(cat, None)
                db.upsert_embedding(old_project, old_items, centroids, counts)

        # ── 2. Determine affected categories in target project ───────────────
        affected_categories = {category}
        if old_category and old_category != category and not (old_project and old_project != project_name):
            affected_categories.add(old_category)

        # ── 3. Upsert item and recompute centroids ───────────────────────────
        new_item = {
            "item_id":   item_id,
            "vector":    vector,
            "category":  category,
            "hierarchy": hierarchy,
            "source":    source,
            "priority":  priority,
            "tagged_at": datetime.now(timezone.utc).isoformat(),
        }
        rec = db.get_embedding(project_name)
        if rec:
            items      = [i for i in rec.get("items", []) if i["item_id"] != item_id]
            items.append(new_item)
            centroids  = dict(rec.get("centroids", {}))
            counts     = dict(rec.get("centroid_counts", {}))
        else:
            items     = [new_item]
            centroids = {}
            counts    = {}

        for cat in affected_categories:
            c = _recompute_centroid(items, cat)
            if c:
                centroids[cat] = c
                counts[cat]    = sum(1 for i in items if i["category"] == cat)
            else:
                centroids.pop(cat, None)
                counts.pop(cat, None)

        db.upsert_embedding(project_name, items, centroids, counts)


def score_item(vector: list, min_count: int = 3) -> list:
    """Score a vector against all stored project centroids. Returns top 5 matches."""
    if not _AVAILABLE or not vector:
        return []
    v       = np.array(vector)
    results = []
    for rec in db.get_all_embeddings():
        project   = rec.get("project", "")
        centroids = rec.get("centroids", {})
        counts    = rec.get("centroid_counts", {})
        for cat, centroid in centroids.items():
            count = counts.get(cat, 0)
            if count < min_count:
                continue
            score = float(np.dot(v, np.array(centroid)))
            results.append({"project": project, "category": cat, "score": score, "count": count})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:5]


def remove_item(item_id: str, project_name: str) -> None:
    """Remove an item from a project and recompute all affected centroids."""
    if not _AVAILABLE:
        return
    with _lock:
        rec = db.get_embedding(project_name)
        if not rec:
            return
        removed_cats = {i["category"] for i in rec.get("items", []) if i["item_id"] == item_id}
        items        = [i for i in rec.get("items", []) if i["item_id"] != item_id]
        centroids    = dict(rec.get("centroids", {}))
        counts       = dict(rec.get("centroid_counts", {}))
        for cat in removed_cats:
            c = _recompute_centroid(items, cat)
            if c:
                centroids[cat] = c
                counts[cat]    = sum(1 for i in items if i["category"] == cat)
            else:
                centroids.pop(cat, None)
                counts.pop(cat, None)
        db.upsert_embedding(project_name, items, centroids, counts)


def get_item_vector(item_id: str):
    """
    Retrieve the stored embedding vector for a specific item_id across all projects.
    Returns None if the item has not been embedded.
    """
    if not _AVAILABLE:
        return None
    for rec in db.get_all_embeddings():
        for item in rec.get("items", []):
            if item["item_id"] == item_id:
                return item["vector"]
    return None


def get_project_stats() -> dict:
    """Return ``{project_name: {total_items, subdivisions}}`` for all stored projects."""
    try:
        stats = {}
        for rec in db.get_all_embeddings():
            name = rec.get("project", "")
            stats[name] = {
                "total_items":  len(rec.get("items", [])),
                "subdivisions": list(rec.get("centroids", {}).keys()),
            }
        return stats
    except Exception:
        return {}
