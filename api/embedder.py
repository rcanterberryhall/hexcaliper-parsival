"""
embedder.py — Sentence-embedding based project classifier.

Loads all-MiniLM-L6-v2 once at module import and provides helpers for
storing per-project item vectors, computing subdivision centroids, and
scoring new items against stored centroids.

All embedding state is stored in the ``embeddings`` table of the TinyDB
database at ``config.DB_PATH``.  A module-level lock serialises writes.

If ``sentence-transformers`` is not installed, ``_AVAILABLE`` is ``False``
and all functions fail silently, leaving the keyword system as sole fallback.
"""
import os
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

from tinydb import TinyDB, Query
import config

_lock = threading.Lock()
_db: "TinyDB | None" = None
_tbl = None
_Q = Query()


def _get_tbl():
    global _db, _tbl
    if _tbl is None:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        _db = TinyDB(config.DB_PATH)
        _tbl = _db.table("embeddings")
    return _tbl


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
    arr = np.array(vectors)
    centroid = np.mean(arr, axis=0)
    norm = np.linalg.norm(centroid)
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
    tbl = _get_tbl()
    Q = _Q
    with _lock:
        # ── 1. Remove from old project if moving cross-project ───────────────
        if old_project and old_project != project_name:
            old_rec = tbl.get(Q.project == old_project)
            if old_rec:
                removed_cats = {i["category"] for i in old_rec.get("items", []) if i["item_id"] == item_id}
                old_items = [i for i in old_rec.get("items", []) if i["item_id"] != item_id]
                centroids = dict(old_rec.get("centroids", {}))
                counts = dict(old_rec.get("centroid_counts", {}))
                for cat in removed_cats:
                    c = _recompute_centroid(old_items, cat)
                    if c:
                        centroids[cat] = c
                        counts[cat] = sum(1 for i in old_items if i["category"] == cat)
                    else:
                        centroids.pop(cat, None)
                        counts.pop(cat, None)
                tbl.update(
                    {"items": old_items, "centroids": centroids, "centroid_counts": counts},
                    Q.project == old_project,
                )

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
        rec = tbl.get(Q.project == project_name)
        if rec:
            items = [i for i in rec.get("items", []) if i["item_id"] != item_id]
            items.append(new_item)
            centroids = dict(rec.get("centroids", {}))
            counts = dict(rec.get("centroid_counts", {}))
            for cat in affected_categories:
                c = _recompute_centroid(items, cat)
                if c:
                    centroids[cat] = c
                    counts[cat] = sum(1 for i in items if i["category"] == cat)
                else:
                    centroids.pop(cat, None)
                    counts.pop(cat, None)
            tbl.update(
                {"items": items, "centroids": centroids, "centroid_counts": counts},
                Q.project == project_name,
            )
        else:
            centroids = {}
            counts = {}
            c = _recompute_centroid([new_item], category)
            if c:
                centroids[category] = c
                counts[category] = 1
            tbl.insert({
                "project":         project_name,
                "items":           [new_item],
                "centroids":       centroids,
                "centroid_counts": counts,
            })


def score_item(vector: list, min_count: int = 3) -> list:
    """Score a vector against all stored project centroids. Returns top 5 matches."""
    if not _AVAILABLE or not vector:
        return []
    tbl = _get_tbl()
    v = np.array(vector)
    results = []
    for rec in tbl.all():
        project = rec.get("project", "")
        centroids = rec.get("centroids", {})
        counts = rec.get("centroid_counts", {})
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
    tbl = _get_tbl()
    Q = _Q
    with _lock:
        rec = tbl.get(Q.project == project_name)
        if not rec:
            return
        removed_cats = {i["category"] for i in rec.get("items", []) if i["item_id"] == item_id}
        items = [i for i in rec.get("items", []) if i["item_id"] != item_id]
        centroids = dict(rec.get("centroids", {}))
        counts = dict(rec.get("centroid_counts", {}))
        for cat in removed_cats:
            c = _recompute_centroid(items, cat)
            if c:
                centroids[cat] = c
                counts[cat] = sum(1 for i in items if i["category"] == cat)
            else:
                centroids.pop(cat, None)
                counts.pop(cat, None)
        tbl.update(
            {"items": items, "centroids": centroids, "centroid_counts": counts},
            Q.project == project_name,
        )


def get_item_vector(item_id: str):
    """
    Retrieve the stored embedding vector for a specific item_id across all projects.
    Returns None if the item has not been embedded (i.e. was never tagged).
    Used by the correlator for embedding-based candidate generation.
    """
    if not _AVAILABLE:
        return None
    tbl = _get_tbl()
    for rec in tbl.all():
        for item in rec.get("items", []):
            if item["item_id"] == item_id:
                return item["vector"]
    return None


def get_project_stats() -> dict:
    """Return ``{project_name: {total_items, subdivisions}}`` for all stored projects."""
    try:
        tbl = _get_tbl()
        stats = {}
        for rec in tbl.all():
            name = rec.get("project", "")
            stats[name] = {
                "total_items":  len(rec.get("items", [])),
                "subdivisions": list(rec.get("centroids", {}).keys()),
            }
        return stats
    except Exception:
        return {}
