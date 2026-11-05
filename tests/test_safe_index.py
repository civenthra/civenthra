import json
import pickle

import pytest

from civenthra.index import BM25, IndexBundle, write_index_manifest


def test_bm25_safe_round_trip(tmp_path):
    path = tmp_path / "bm25.json.gz"
    original = BM25().build([["火种", "知识"], ["离线", "知识", "知识"]])

    original.save(path)
    restored = BM25.load(path)

    assert restored.N == original.N
    assert list(restored.dlen) == list(original.dlen)
    assert restored.search("知识", k=2) == original.search("知识", k=2)


def test_legacy_pickle_is_rejected_without_deserializing(tmp_path):
    marker = tmp_path / "executed.txt"

    class Payload:
        def __reduce__(self):
            return (marker.write_text, ("unsafe",))

    path = tmp_path / "bm25.pkl"
    path.write_bytes(pickle.dumps(Payload()))

    with pytest.raises(RuntimeError, match="pickle"):
        BM25.load(path)
    assert not marker.exists()


def test_invalid_safe_index_is_rejected(tmp_path):
    path = tmp_path / "bm25.json.gz"
    path.write_bytes(b"not-gzip")

    with pytest.raises(RuntimeError, match="损坏|无效"):
        BM25.load(path)


def test_invalid_posting_shape_is_rejected(tmp_path):
    import gzip

    path = tmp_path / "bm25.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump({
            "schema": 2, "k1": 1.5, "b": 0.75, "N": 1, "avgdl": 1.0,
            "dlen": [1], "postings": {"x": [99, 1]}, "df": {"x": 1},
        }, stream)

    with pytest.raises(RuntimeError, match="结构无效"):
        BM25.load(path)


def test_index_bundle_rejects_tampered_files(tmp_path):
    bm = BM25().build([["safe"]])
    bm.save(tmp_path / "bm25.json.gz")
    (tmp_path / "chunks.jsonl").write_text(
        json.dumps({"text": "safe", "book_no": 1}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "meta.json").write_text('{"books": []}', encoding="utf-8")
    write_index_manifest(tmp_path)

    IndexBundle.load(tmp_path)
    (tmp_path / "chunks.jsonl").write_text('{"text":"changed"}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="完整性"):
        IndexBundle.load(tmp_path)
