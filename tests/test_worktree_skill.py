"""Навык worktree на временных репо: исполняется блок «1. Создать worktree» из SKILL.md как есть.

HOME подменён, ~/.claude/skills указывает на skills/ этого репо. У каждой ветки свой коммит,
поэтому по HEAD нового дерева видно, от какой базы оно пошло.

  python3 -m unittest discover -s tests
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "worktree", "SKILL.md")


def create_block():
    """Первый ```bash-блок раздела «### 1. Создать worktree»."""
    with open(SKILL, encoding="utf-8") as f:
        text = f.read()
    section = re.search(r"^### 1\. Создать worktree\n(.*?)(?=^### )", text, re.S | re.M)
    assert section, "в SKILL.md нет раздела «### 1. Создать worktree»"
    block = re.search(r"```bash\n(.*?)```", section.group(1), re.S)
    assert block, "в разделе создания нет ```bash-блока"
    return block.group(1)


class WorktreeSkill(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="worktree-skill-"))
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        os.symlink(os.path.join(ROOT, "skills"), os.path.join(self.home, ".claude", "skills"))
        self.env = {
            "HOME": self.home, "PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args):
        p = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, f"git {' '.join(args)}: {p.stderr}")
        return p.stdout.strip()

    def make_repo(self, branches, name="repo"):
        """Репо с ветками branches, у каждой свой коммит; HEAD на первой. -> (путь, {ветка: коммит})."""
        repo = os.path.join(self.tmp, name)
        os.makedirs(repo)
        self.git(repo, "init", "-q", f"--initial-branch={branches[0]}")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "init")
        init, heads = self.git(repo, "rev-parse", "HEAD"), {}
        for b in branches:
            self.git(repo, "checkout", "-q", "-B", b, init)
            self.git(repo, "commit", "-q", "--allow-empty", "-m", b)
            heads[b] = self.git(repo, "rev-parse", "HEAD")
        self.git(repo, "checkout", "-q", branches[0])
        return repo, heads

    def run_skill(self, repo, name="task"):
        return subprocess.run(["bash", "-c", create_block()], cwd=repo, env=dict(self.env, ARGUMENTS=name),
                              capture_output=True, text=True, timeout=60)

    def assert_worktree(self, repo, name, want_commit, p):
        wt = os.path.join(repo, ".claude", "worktrees", name)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.isdir(wt), f"нет {wt}:\n{p.stdout}{p.stderr}")
        self.assertEqual(self.git(wt, "branch", "--show-current"), f"feat/{name}")
        self.assertEqual(self.git(wt, "rev-parse", "HEAD"), want_commit)
        self.assertIn(os.path.realpath(wt), [os.path.realpath(line.split(" ", 1)[1])
                                             for line in self.git(repo, "worktree", "list", "--porcelain").splitlines()
                                             if line.startswith("worktree ")])

    def test_base_without_remote(self):
        variants = [  # ветки репо -> ожидаемая база
            (["dev", "main", "master"], "dev"),
            (["main", "dev"], "dev"),
            (["main", "master"], "main"),
            (["master", "main"], "main"),
            (["master"], "master"),
            (["dev", "master"], "dev"),
        ]
        for n, (branches, want) in enumerate(variants):
            with self.subTest(branches=branches):
                repo, heads = self.make_repo(branches, name=f"repo{n}")
                self.assert_worktree(repo, "task", heads[want], self.run_skill(repo))

    def test_no_known_branch_falls_back_to_current(self):
        repo, heads = self.make_repo(["trunk"])
        p = self.run_skill(repo)
        self.assert_worktree(repo, "task", heads["trunk"], p)
        self.assertIn("trunk", p.stderr)

    def test_fresh_origin_base_and_no_upstream(self):
        """Локальный dev отстал от origin/dev: ветка идёт от origin/dev и не следит за ним."""
        origin, heads = self.make_repo(["main", "dev"], name="origin")
        repo = os.path.join(self.tmp, "clone")
        self.git(self.tmp, "clone", "-q", origin, repo)
        self.git(repo, "branch", "-q", "dev", "origin/dev")
        self.git(origin, "checkout", "-q", "dev")
        self.git(origin, "commit", "-q", "--allow-empty", "-m", "dev ушёл вперёд")
        fresh = self.git(origin, "rev-parse", "HEAD")
        p = self.run_skill(repo)
        self.assert_worktree(repo, "task", fresh, p)
        wt = os.path.join(repo, ".claude", "worktrees", "task")
        upstream = subprocess.run(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=wt, env=self.env,
                                  capture_output=True, text=True)
        self.assertNotEqual(upstream.returncode, 0, f"upstream выставлен: {upstream.stdout}")

    def test_dev_only_in_origin(self):
        """Свежий клон: локально только main, dev есть лишь в origin — база всё равно dev."""
        origin, heads = self.make_repo(["main", "dev"], name="origin")
        repo = os.path.join(self.tmp, "clone")
        self.git(self.tmp, "clone", "-q", origin, repo)
        self.assert_worktree(repo, "task", heads["dev"], self.run_skill(repo))

    def test_worktree_dir_ignored(self):
        repo, _ = self.make_repo(["main"])
        self.assertEqual(self.run_skill(repo).returncode, 0)
        status = self.git(repo, "status", "--porcelain")
        self.assertNotIn(".claude/", status)


if __name__ == "__main__":
    unittest.main()
