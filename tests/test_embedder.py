"""tests/test_embedder.py — Tests for the embedding-based project classifier."""
import pytest
import numpy as np


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_model(monkeypatch):
    """Replace model inference with a fixed unit-normalised vector and clear state after each test."""
    import numpy as np
    import embedder

    fake_vec = np.random.randn(384)
    fake_vec /= np.linalg.norm(fake_vec)

    class _FakeModel:
        def encode(self, text, **kw):
            return fake_vec

    monkeypatch.setattr(embedder, "_model", _FakeModel(), raising=False)
    monkeypatch.setattr(embedder, "_AVAILABLE", True)
    yield
    embedder._get_tbl().truncate()


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_embed_returns_384_floats():
    import embedder
    vec = embedder.embed("some text")
    assert len(vec) == 384
    assert all(isinstance(f, float) for f in vec)


def test_embed_unit_normalized():
    import embedder
    vec = embedder.embed("some text")
    norm = np.linalg.norm(vec)
    assert abs(norm - 1.0) < 1e-5


def test_update_project_creates_record():
    import embedder
    from tinydb import Query
    vec = np.random.randn(384)
    vec /= np.linalg.norm(vec)
    embedder.update_project("Alpha", "item1", vec.tolist(), "task", "user", "github", "high")
    rec = embedder._get_tbl().get(Query().project == "Alpha")
    assert rec is not None
    assert len(rec["items"]) == 1
    assert rec["items"][0]["item_id"] == "item1"


def test_update_project_recomputes_centroid():
    import embedder
    from tinydb import Query
    v1 = np.zeros(384); v1[0] = 1.0
    v2 = np.zeros(384); v2[1] = 1.0
    embedder.update_project("Beta", "item1", v1.tolist(), "task", "user", "slack", "medium")
    embedder.update_project("Beta", "item2", v2.tolist(), "task", "user", "slack", "medium")
    rec = embedder._get_tbl().get(Query().project == "Beta")
    centroid = np.array(rec["centroids"]["task"])
    expected = v1 + v2
    expected /= np.linalg.norm(expected)
    assert np.allclose(centroid, expected, atol=1e-5)


def test_update_project_moves_between_projects():
    import embedder
    from tinydb import Query
    vec = np.random.randn(384)
    vec /= np.linalg.norm(vec)
    embedder.update_project("ProjA", "item1", vec.tolist(), "task", "user", "github", "high")
    embedder.update_project("ProjB", "item1", vec.tolist(), "task", "user", "github", "high",
                            old_project="ProjA")
    tbl = embedder._get_tbl()
    rec_a = tbl.get(Query().project == "ProjA")
    rec_b = tbl.get(Query().project == "ProjB")
    assert rec_a is None or all(i["item_id"] != "item1" for i in rec_a.get("items", []))
    assert rec_b is not None
    assert any(i["item_id"] == "item1" for i in rec_b["items"])


def test_update_project_moves_between_subdivisions():
    import embedder
    from tinydb import Query
    vec = np.random.randn(384)
    vec /= np.linalg.norm(vec)
    embedder.update_project("Gamma", "item1", vec.tolist(), "task", "user", "github", "high")
    embedder.update_project("Gamma", "item1", vec.tolist(), "fyi", "user", "github", "low",
                            old_category="task")
    rec = embedder._get_tbl().get(Query().project == "Gamma")
    assert "task" not in rec["centroids"]
    assert "fyi" in rec["centroids"]
    assert rec["items"][0]["category"] == "fyi"


def test_score_item_returns_empty_below_min_count():
    import embedder
    vec = np.random.randn(384)
    vec /= np.linalg.norm(vec)
    for i in range(2):
        v = np.random.randn(384)
        v /= np.linalg.norm(v)
        embedder.update_project("Delta", f"item{i}", v.tolist(), "task", "user", "github", "high")
    result = embedder.score_item(vec.tolist(), min_count=3)
    assert result == []


def test_score_item_returns_correct_ranking():
    import embedder
    target = np.zeros(384); target[0] = 1.0

    close_vec = np.zeros(384); close_vec[0] = 0.9; close_vec[1] = 0.1
    close_vec /= np.linalg.norm(close_vec)

    far_vec = np.zeros(384); far_vec[1] = 1.0

    for i in range(3):
        v = close_vec + np.random.randn(384) * 0.01
        v /= np.linalg.norm(v)
        embedder.update_project("Near", f"near{i}", v.tolist(), "task", "user", "github", "high")

    for i in range(3):
        v = far_vec + np.random.randn(384) * 0.01
        v /= np.linalg.norm(v)
        embedder.update_project("Far", f"far{i}", v.tolist(), "task", "user", "github", "high")

    results = embedder.score_item(target.tolist(), min_count=3)
    assert len(results) >= 2
    assert results[0]["project"] == "Near"
    assert results[0]["score"] > results[-1]["score"]


def test_remove_item_removes_and_recomputes():
    import embedder
    from tinydb import Query
    v1 = np.zeros(384); v1[0] = 1.0
    v2 = np.zeros(384); v2[1] = 1.0
    embedder.update_project("Echo", "item1", v1.tolist(), "task", "user", "github", "high")
    embedder.update_project("Echo", "item2", v2.tolist(), "task", "user", "github", "high")
    embedder.remove_item("item1", "Echo")
    rec = embedder._get_tbl().get(Query().project == "Echo")
    assert all(i["item_id"] != "item1" for i in rec["items"])
    centroid = np.array(rec["centroids"]["task"])
    assert np.allclose(centroid, v2, atol=1e-5)


def test_get_project_stats():
    import embedder
    for i in range(3):
        v = np.random.randn(384)
        v /= np.linalg.norm(v)
        embedder.update_project("Stats", f"item{i}", v.tolist(),
                                "task" if i < 2 else "fyi", "user", "github", "high")
    stats = embedder.get_project_stats()
    assert "Stats" in stats
    assert stats["Stats"]["total_items"] == 3
    assert set(stats["Stats"]["subdivisions"]) == {"task", "fyi"}
