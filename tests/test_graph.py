"""Synthetic offline regressions; no book excerpts or inference services needed."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from civenthra.evidence import Source, compact, file_digest, locate_in_sources, verify, same_cleaned_text
from civenthra.graph import GraphStore, build, extract, merge_passages
from civenthra.textutil import clean_text


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.index, self.library, self.graph = [self.root / n for n in ("index", "library", "graph")]
        self.index.mkdir()
        self.folder = self.library / "1_test"
        (self.folder / "mt").mkdir(parents=True)
        self.raw = "# Test chapter\r\n\r\n通风对于病室的空气具有重要意义，作者讨论通风与卫生之间的联系。\r\n\r\n保持空气流通，观察室内变化；这里仅是历史文本，并不是现代医学建议。"
        self.source = self.folder / "mt" / "ch01.txt"
        self.source.write_bytes(self.raw.encode())
        self.text = clean_text(self.raw).replace("# Test chapter", "").strip()
        self.record = dict(i=0, no=1, title="Test book", author="Test author", pillar="body",
                           chapter="Test chapter", lang="zh", text=self.text)
        self.write(self.index / "chunks.jsonl", self.record, lines=True)
        self.write(self.index / "meta.json", {"books": [{"no": 1, "dir": "1_test", "source": "mt"}]})
        self.write(self.folder / "meta.json", {"no": 1, "stage": "typeset"})
        self.canon, self.schema = self.root / "canon.json", self.root / "schema.json"
        self.write(self.canon, {"pillars": [{"id": "body", "name": "Body"}], "books": [
            {"no": 1, "title": "Test book", "author": "Test author", "pillar": "body"}]})
        self.write(self.schema, {"pilot_books": [1], "predicates": ["describes"], "concepts": [
            {"id": "air", "label": "通风", "aliases": ["通风"], "books": [1]}]})
        self.rebuild()

    @staticmethod
    def write(path, value, lines=False):
        path.write_text(json.dumps(value, ensure_ascii=False) + ("\n" if lines else ""), encoding="utf-8")

    def rebuild(self):
        build(self.index, self.library, self.graph, self.canon, self.schema)
        self.store = GraphStore.load(self.index, self.graph)
        self.assertIsNotNone(self.store)

    def passage(self):
        return self.store.search("通风")[0]

    def claim(self):
        return {"subject": "concept:air", "predicate": "describes", "object": "讨论病室空气",
                "quote": "通风对于病室的空气具有重要意义"}

    def test_locator_exact_raw_and_no_fake_pages(self):
        passage = self.passage()
        self.assertTrue(verify(passage))
        self.assertEqual(passage["raw_excerpt"], self.raw[passage["raw_start"]:passage["raw_end"]])
        self.assertEqual(compact(clean_text(passage["raw_excerpt"])), compact(self.text))
        self.assertNotIn("page", passage)
        self.assertEqual(passage["content_status"], "draft")

    def test_whitespace_does_not_merge_words_or_numbers(self):
        self.assertFalse(same_cleaned_text("now here", "nowhere"))
        self.assertFalse(same_cleaned_text("100 200", "100200"))
        self.assertTrue(same_cleaned_text("通风\n\n卫生", "通风\n卫生"))

    def test_ambiguous_and_missing_not_guessed(self):
        self.source.write_bytes((self.raw + "\r\n" + self.raw).encode())
        source = Source(self.source)
        self.assertEqual(locate_in_sources(self.text, [source])["locator_status"], "ambiguous")
        self.assertEqual(locate_in_sources("不存在的原文内容不能被编造", [source])["locator_status"], "unresolved")

    def test_changed_source_excluded(self):
        passage = self.passage()
        self.source.write_bytes((self.raw + "changed").encode())
        self.assertFalse(verify(passage))
        self.assertEqual(self.store.search("通风"), [])
        self.assertEqual(self.store.enrich(self.record)["locator_status"], "unverified")

    def test_candidate_is_not_fact_and_review_persists(self):
        pid = self.passage()["id"]
        cid = self.store.add_claim(pid, self.claim(), "mock")
        self.assertEqual(self.store.claims()[0]["status"], "candidate")
        before = [p["id"] for p in self.store.search("通风")]
        self.store.review(cid, "approved", "test reviewer")
        self.assertEqual(before, [p["id"] for p in self.store.search("通风")])
        self.rebuild()
        self.assertEqual(self.store.claims("approved")[0]["id"], cid)

    def test_reindex_preserves_only_unchanged_evidence_decisions(self):
        cid = self.store.add_claim(self.passage()["id"], self.claim(), "mock")
        self.store.review(cid, "approved", "test reviewer")
        changed = dict(self.record, i=99)
        self.write(self.index / "chunks.jsonl", changed, lines=True)
        self.rebuild()
        self.assertEqual(self.store.claims("approved")[0]["id"], cid)
        self.source.write_bytes((self.raw + "source version changed").encode())
        self.rebuild()
        self.assertEqual(self.store.claims("approved"), [])

    def test_obsidian_links_and_existing_notes_preserved(self):
        from civenthra.obsidian import export_vault
        import re
        self.store.add_claim(self.passage()["id"], self.claim(), "mock")
        target = self.root / "vault"
        result = export_vault(self.store, target, evidence_per_book=1)
        self.assertEqual(result["books"], 1)
        self.assertEqual(result["evidence_samples"], 1)
        self.assertEqual(result["claims"], 1)
        notes = list(target.rglob("*.md"))
        for note in notes:
            for path in re.findall(r"\[\[([^|\]]+)\|[^\]]*\]\]", note.read_text(encoding="utf-8")):
                self.assertTrue((target / (path + ".md")).is_file(), path)
        home = Path(result["home"])
        home.write_text("My own annotations", encoding="utf-8")
        with self.assertRaises(ValueError):
            export_vault(self.store, target)
        self.assertEqual(home.read_text(encoding="utf-8"), "My own annotations")
        self.assertFalse((target / ".obsidian" / "plugins").exists())

    def test_invalid_claims_rejected(self):
        pid = self.passage()["id"]
        for fields in ({"quote": "完全捏造的引用不允许通过"}, {"predicate": "causes"},
                       {"subject": "concept:invented"}, {"object": ""},
                       {"quote": "保持空气流通，观察室内变化"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.store.add_claim(pid, dict(self.claim(), **fields), "mock")
        self.assertEqual(self.store.claims(), [])

    def test_stale_source_cannot_be_approved(self):
        cid = self.store.add_claim(self.passage()["id"], self.claim(), "mock")
        self.source.write_bytes((self.raw + "changed").encode())
        with self.assertRaises(ValueError):
            self.store.review(cid, "approved", "reviewer")

    def test_failed_build_keeps_snapshot_and_index(self):
        manifest = (self.graph / "current.json").read_bytes()
        sha = file_digest(self.index / "chunks.jsonl")
        self.write(self.index / "meta.json", {"books": [{"no": 1, "dir": "../../escape", "source": "mt"}]})
        with self.assertRaises(ValueError):
            self.rebuild()
        self.assertEqual(manifest, (self.graph / "current.json").read_bytes())
        self.assertEqual(sha, file_digest(self.index / "chunks.jsonl"))

    def test_changed_index_and_manifest_path_rejected(self):
        self.index.joinpath("chunks.jsonl").write_text("{}\n", encoding="utf-8")
        self.assertIsNone(GraphStore.load(self.index, self.graph))
        self.write(self.graph / "current.json", {"schema": 1, "snapshot": "../outside.sqlite3"})
        self.assertIsNone(GraphStore.load(self.index, self.graph))

    def test_merge_keeps_baseline_caps_and_deduplicates(self):
        base = [dict(self.record, text="text" + str(i), no=i // 2) for i in range(6)]
        merged = merge_passages(base, [base[0], dict(base[0], no=9)], 6)
        self.assertEqual(merged[:2], base[:2])
        self.assertEqual(len(merged), 6)
        self.assertEqual(merge_passages(base, [], 6), base)
        self.assertTrue(all(sum(p["no"] == no for p in merged) <= 2 for no in {p["no"] for p in merged}))

    def test_bounded_local_extraction_and_resume(self):
        server = Mock(model="mock")
        server.chat.return_value = json.dumps([self.claim()], ensure_ascii=False)
        with patch("civenthra.airgap.engaged", return_value=True):
            result = extract(self.store, server=server, max_passages=1)
            self.assertEqual(result["accepted_candidates"], 1)
            self.assertEqual(extract(self.store, server=server)["passages_processed"], 0)
        server.chat.assert_called_once()
        server.start.assert_not_called()
        self.rebuild()
        with patch("civenthra.airgap.engaged", return_value=True):
            self.assertEqual(extract(self.store, server=server)["passages_processed"], 0)

    def test_ask_and_stream_share_retrieval_and_fallback(self):
        from civenthra.rag import Civenthra
        kb = Civenthra.__new__(Civenthra)
        kb.graph, kb.graph_mode, kb.top_k, kb.cap_per_book = self.store, "hybrid", 6, 2
        kb.index = Mock()
        kb.index.search.return_value = [self.record]
        kb.server = Mock()
        kb.server.chat.return_value = "test answer [1]"
        kb.server.stream.return_value = iter(["test answer"])
        answer = kb.ask("通风")
        self.assertEqual(answer["citations"][0]["locator_status"], "located")
        self.assertEqual(next(kb.stream_ask("通风"))["passages"][0]["evidence_id"],
                         answer["passages"][0]["evidence_id"])
        with patch.object(self.store, "enrich", side_effect=OSError("test failure")), self.assertLogs("civenthra.rag", "WARNING"):
            self.assertEqual(kb.retrieve("通风"), [self.record])
        kb.graph = None
        self.assertEqual(kb.retrieve("通风"), [self.record])

    def test_readonly_graph_http(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import build_opener, ProxyHandler, Request
        from urllib.error import HTTPError
        from civenthra.serve import Handler
        token = "graph-test-token"
        handler = type("TestHandler", (Handler,), {
            "kb": Mock(graph=self.store),
            "session_token": token,
            "csp_nonce": "graph-test-nonce",
        })
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = build_opener(ProxyHandler({}))
        try:
            url = "http://127.0.0.1:%s" % server.server_port
            def api(path):
                return Request(url + path, headers={"X-Civenthra-Token": token})

            with opener.open(api("/api/graph/status"), timeout=3) as response:
                self.assertTrue(json.load(response)["available"])
            with opener.open(api("/api/graph/evidence?id=" + self.passage()["id"]), timeout=3) as response:
                self.assertTrue(json.load(response)["verified"])
            with self.assertRaises(HTTPError) as failure:
                opener.open(api("/api/graph/evidence?id=../../private"), timeout=3)
            self.assertEqual(failure.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
