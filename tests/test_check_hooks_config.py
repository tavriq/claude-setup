"""check-hooks-config на поддельных settings: мёртвые matcher'ы, опечатки, пропавшие скрипты.

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
LINTER = os.path.join(ROOT, "bin", "check-hooks-config")


class CheckHooksConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hooks-lint-test-")
        self.claude = os.path.join(self.tmp, "claude")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, ".claude", "hooks"))
        os.makedirs(self.claude)
        self.script = os.path.join(self.proj, ".claude", "hooks", "guard.py")
        with open(self.script, "w") as f:
            f.write("print()\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, hooks, where="project", raw=None):
        path = os.path.join(self.proj, ".claude", "settings.json") if where == "project" \
            else os.path.join(self.claude, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps({"hooks": hooks}))

    def lint(self, *args, stdin=None):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.claude)
        return subprocess.run([sys.executable, LINTER, *args], input=stdin, capture_output=True, text=True,
                              env=env, timeout=20)

    def ok_hook(self, matcher="Bash", **extra):
        return {"matcher": matcher, "hooks": [dict({"type": "command",
                                                   "command": "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/guard.py"},
                                                  **extra)]}

    def test_clean_config_is_silent(self):
        self.settings({"PreToolUse": [self.ok_hook("Bash|Edit|mcp__github__create_pr")],
                       "SessionStart": [self.ok_hook("startup|clear")]})
        r = self.lint(self.proj)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_command_pattern_in_matcher_never_fires(self):
        self.settings({"PreToolUse": [self.ok_hook("Bash(git push:*)")]})
        r = self.lint(self.proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("не сработает никогда", r.stdout)
        self.assertIn("поле if", r.stdout)

    def test_tool_name_typo(self):
        self.settings({"PostToolUse": [self.ok_hook("Bsh|Edit")]})
        self.assertIn("нет тула «Bsh»", self.lint(self.proj).stdout)

    def test_session_start_values(self):
        self.settings({"SessionStart": [self.ok_hook("startup|boot")]})
        self.assertIn("нет значений boot", self.lint(self.proj).stdout)

    def test_if_field(self):
        self.settings({"PreToolUse": [self.ok_hook("Bash", **{"if": "Bash(git push:*)"}),
                                      self.ok_hook("Bash", **{"if": "git push"})]})
        out = self.lint(self.proj).stdout
        self.assertEqual(out.count("if «"), 1)
        self.assertIn("if «git push»", out)

    def test_missing_and_non_executable_files(self):
        plain = os.path.join(self.proj, "hook.sh")
        with open(plain, "w") as f:
            f.write("#!/bin/sh\n")
        self.settings({"Stop": [{"hooks": [{"type": "command", "command": "python3 ~/nope/missing.py"},
                                           {"type": "command", "command": plain},
                                           {"type": "command", "command": "no-such-binary-xyz --flag"},
                                           {"type": "command", "command": "python3 -c 'print(1)'"}]}]})
        out = self.lint(self.proj).stdout
        self.assertIn("нет скрипта", out)
        self.assertIn("не исполняемый", out)
        self.assertIn("нет в PATH: no-such-binary-xyz", out)
        self.assertEqual(out.count("\n"), 3)

    def test_relative_script_resolved_from_project_root(self):
        self.settings({"Stop": [{"hooks": [
            {"type": "command", "command": "python3 .claude/hooks/guard.py"},
            {"type": "command", "command": "env -i FOO=1 python3 .claude/hooks/absent.py"},
        ]}]})
        out = self.lint(self.proj).stdout
        self.assertEqual(out.count("\n"), 1)
        self.assertIn("нет скрипта", out)
        self.assertIn("absent.py", out)

    def test_broken_json_switches_off_whole_file(self):
        self.settings(None, where="user", raw="{ битый")
        self.assertIn("JSON не читается", self.lint(self.proj).stdout)

    def test_hook_mode(self):
        self.settings({"PreToolUse": [self.ok_hook("Bash(rm:*)")]})
        out = json.loads(self.lint("--hook", stdin=json.dumps({"cwd": self.proj})).stdout)
        self.assertIn("Bash(rm:*)", out["systemMessage"])

    def test_hook_mode_silent_when_clean(self):
        self.settings({"PreToolUse": [self.ok_hook()]})
        self.assertEqual(self.lint("--hook", stdin=json.dumps({"cwd": self.proj})).stdout, "")


if __name__ == "__main__":
    unittest.main()
