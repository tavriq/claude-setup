"""Репо публичное: ни локальных путей, ни слов из личного стоп-листа ни в одном файле.

Стоп-лист живёт вне репо — ~/.config/tavriq/stopwords или $TAVRIQ_STOPWORDS (одна регулярка на строку,
# — комментарий): сам список в коммит не попадает. Без файла проверяются только локальные пути.
Шаблон пути склеен, чтобы этот файл не находил сам себя.
"""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".venv"}
WORDS_FILE = Path(os.environ.get("TAVRIQ_STOPWORDS", "~/.config/tavriq/stopwords")).expanduser()


def stopwords():
    if not WORDS_FILE.is_file():
        return []
    lines = (line.strip() for line in WORDS_FILE.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def text_files():
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if path.is_file() and not SKIP_DIRS.intersection(rel.parts):
            try:
                yield rel.as_posix(), path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue


class Hygiene(unittest.TestCase):
    def test_no_private_words_or_home_paths(self):
        forbidden = re.compile("|".join(["(?-i:/Us" + "ers/[a-z])", "(?-i:/ho" + "me/[a-z])", *stopwords()]), re.IGNORECASE)
        hits = [f"{rel}:{n}: {m.group(0)}"
                for rel, text in text_files()
                for n, line in enumerate(text.splitlines(), 1)
                for m in forbidden.finditer(line)]
        self.assertEqual(hits, [])

    def test_stopwords_file_is_read(self):
        if not WORDS_FILE.is_file():
            self.skipTest(f"{WORDS_FILE} нет: проверены только локальные пути")
        self.assertTrue(stopwords())


if __name__ == "__main__":
    unittest.main()
