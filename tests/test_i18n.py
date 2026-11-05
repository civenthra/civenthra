import unittest

from civenthra.i18n import detect_locale, normalize_locale
from civenthra.rag import build_context, build_messages, format_citations


PASSAGE = {
    "title": "Example Source",
    "author": "A. Writer",
    "chapter": "Chapter 1",
    "lang": "en",
    "text": "Evidence remains inspectable.",
}


class LocaleTest(unittest.TestCase):
    def test_detects_supported_languages(self):
        self.assertEqual(detect_locale("What evidence supports this?"), "en")
        self.assertEqual(detect_locale("这条结论有什么依据？"), "zh-CN")
        self.assertEqual(normalize_locale("en-US"), "en")
        self.assertEqual(normalize_locale("zh_Hans"), "zh-CN")

    def test_rejects_unknown_language(self):
        with self.assertRaises(ValueError):
            normalize_locale("fr")

    def test_english_prompt_and_context_are_selected(self):
        messages = build_messages("What does the source say?", [PASSAGE])
        self.assertIn("Your only evidence", messages[0]["content"])
        self.assertIn("SOURCE PASSAGES", messages[1]["content"])
        self.assertIn("English original", build_context([PASSAGE], "en"))

    def test_chinese_prompt_remains_available(self):
        messages = build_messages("资料说了什么？", [PASSAGE])
        self.assertIn("你的全部依据", messages[0]["content"])
        self.assertIn("【文明穹顶文献】", messages[1]["content"])

    def test_english_citation_labels(self):
        citation = format_citations([{**PASSAGE, "score": 0.8}], language="en")
        self.assertIn("English original", citation)
        self.assertIn("relevance 0.80", citation)


if __name__ == "__main__":
    unittest.main()
