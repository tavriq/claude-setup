"""cc-who на поддельном каталоге Claude Code: реестр сессий + истории с /rename.

  python3 -m unittest discover -s tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CC_WHO = os.path.join(ROOT, "bin", "cc-who")


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


class CcWho(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cc-who-test-")
        os.makedirs(os.path.join(self.home, "sessions"))
        os.makedirs(os.path.join(self.home, "projects", "-work-app"))
        self.live = os.getpid()  # живой процесс — сам тест

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def session(self, key, sid, addr, pid=None, status="idle", name_source="derived", cwd="/work/app"):
        with open(os.path.join(self.home, "sessions", f"{key}.json"), "w") as f:
            json.dump({"pid": pid or self.live, "sessionId": sid, "name": addr, "nameSource": name_source,
                       "status": status, "cwd": cwd}, f)

    def history(self, sid, *records, pad_kb=0):
        path = os.path.join(self.home, "projects", "-work-app", sid + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if pad_kb:
                f.write(json.dumps({"type": "user", "text": "x" * pad_kb * 1024}) + "\n")
        return path

    def run_who(self, *args, stdin=None, env_extra=None):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, CC_WHO, *args], input=stdin, capture_output=True, text=True,
                              env=env, timeout=20)

    def test_name_from_rename_maps_to_current_address(self):
        self.session("1", "s-lead", "app-4c")
        self.history("s-lead", {"type": "custom-title", "customTitle": "техлид"})
        self.assertEqual(self.run_who("техлид").stdout.strip(), "app-4c")

    def test_agent_name_wins_and_last_rename_counts(self):
        self.session("1", "s-dev", "app-7a")
        self.history("s-dev", {"type": "custom-title", "customTitle": "черновик"},
                     {"type": "agent-name", "agentName": "дев-1"},
                     {"type": "agent-name", "agentName": "дев-2"})
        self.assertEqual(self.run_who("дев-2").stdout.strip(), "app-7a")
        self.assertEqual(self.run_who("дев-1").returncode, 1)

    def test_word_in_message_text_is_not_a_rename(self):
        self.session("1", "s-x", "app-1b")
        self.history("s-x", {"type": "user", "text": '{"type":"custom-title","customTitle":"фальшивка"}'})
        self.assertEqual(self.run_who("фальшивка").returncode, 1)

    def test_dead_sessions_are_skipped(self):
        self.session("1", "s-old", "app-0z", pid=dead_pid())
        self.history("s-old", {"type": "custom-title", "customTitle": "техлид"})
        self.assertEqual(self.run_who("техлид").returncode, 1)

    def test_exact_match_beats_substring_and_ambiguity_is_reported(self):
        for i, (sid, title) in enumerate([("a", "дев-1"), ("b", "дев-10"), ("c", "дев-2")]):
            self.session(str(i), sid, f"app-{i}")
            self.history(sid, {"type": "custom-title", "customTitle": title})
        self.assertEqual(self.run_who("дев-1").stdout.strip(), "app-0")
        r = self.run_who("дев")
        self.assertEqual(r.returncode, 2)
        self.assertIn("уточни", r.stderr)

    def test_new_rename_is_read_from_appended_tail(self):
        self.session("1", "s-a", "app-2c")
        self.history("s-a", {"type": "custom-title", "customTitle": "старое"}, pad_kb=30)
        self.assertEqual(self.run_who("старое").stdout.strip(), "app-2c")
        self.history("s-a", {"type": "custom-title", "customTitle": "новое"})
        self.assertEqual(self.run_who("новое").stdout.strip(), "app-2c")
        with open(os.path.join(self.home, "cache", "cc-who.json")) as f:
            self.assertTrue(json.load(f))

    def test_name_after_clear_comes_from_registry(self):
        self.session("1", "s-fresh", "портфолио", name_source="user")
        self.assertEqual(self.run_who("портфолио").stdout.strip(), "портфолио")

    def test_table_marks_duplicates_and_empty_context(self):
        self.session("1", "s-a", "app-1", status="busy")
        self.session("2", "s-b", "app-2")
        self.history("s-a", {"type": "custom-title", "customTitle": "техлид"}, pad_kb=25)
        self.history("s-b", {"type": "custom-title", "customTitle": "Техлид"})
        out = self.run_who().stdout
        self.assertEqual(out.count("⚠дубль"), 2)
        self.assertIn("занят", out)
        self.assertIn("пустой", out)

    def test_me_and_hook(self):
        self.session("1", "s-me", "app-9q")
        self.history("s-me", {"type": "custom-title", "customTitle": "ревьюер"})
        me = self.run_who("--me", env_extra={"CLAUDE_CODE_SESSION_ID": "s-me"})
        self.assertIn("ревьюер", me.stdout)
        out = json.loads(self.run_who("--hook", stdin=json.dumps({"session_id": "s-me"})).stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("«ревьюер»", ctx)
        self.assertIn("app-9q", ctx)

    def test_hook_survives_garbage(self):
        r = self.run_who("--hook", stdin="не json")
        self.assertEqual(r.returncode, 0)
        self.assertIn("нет имени роли", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
