import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def run(language):
    output = subprocess.check_output(
        [sys.executable, "examples/synthetic_demo.py", "--lang", language],
        cwd=ROOT, text=True, encoding="utf-8",
    )
    return json.loads(output)


def test_bilingual_synthetic_golden_path():
    for language in ("en", "zh-CN"):
        result = run(language)
        assert result["language"] == language
        assert result["sources"]
        assert result["contains_private_or_book_text"] is False
