#!/usr/bin/env python3
"""bash-guard — хук PreToolUse для Bash: опасную команду останавливает код, а не просьба в CLAUDE.md.

Уровни:
  block   — отказ. Маркером не снимается: нужно — человек делает руками или выключает страж.
  confirm — отказ, пока в команде нет `#danger-ok: <дословная цитата согласия человека>`.
            Это тормоз и след в логе, а не замок: цитату агент может и выдумать, поэтому
            каждый пропуск по маркеру пишется в лог вместе с цитатой.
  ask     — окно подтверждения человеку (операции на хостах из prod_hosts, свои правила).

Разбор — свой лексер, без shlex: один проход по строке с учётом кавычек и экранирования.
Комментарии отбрасываются, тела heredoc привязываются к своей команде (в теле без кавычек
у разделителя ищутся $(…) и `…`), команда делится на простые команды по ; && || | & ( )
и переводам строк. Впереди снимаются VAR=… и обёртки (sudo, env, nohup, timeout, xargs…).
Вложенные команды разбираются тем же лексером: $(…) и `…` вне одинарных кавычек, bash -c '…',
eval, ssh <хост> '…' (и heredoc в ssh/bash), docker exec, find -exec. cd меняет каталог
для следующих команд.

Конфиг: ~/.claude/bash-guard.json и <корень репо>/.claude/bash-guard.json (образец —
bash-guard.example.json). Лог: ~/.claude/logs/bash-guard.jsonl, секреты в команде маскируются.
Выключатель: файл ~/.claude/bash-guard.off. Сбой разбора или битый конфиг — пропуск (fail-open):
страж не должен останавливать работу из-за своей ошибки, от поломки страхуют тесты.

Тесты: python3 -m unittest discover -s tests
"""
import itertools
import json
import os
import re
import sys
import time
from pathlib import Path

HOME = str(Path.home())
CLAUDE_DIR = os.path.join(HOME, ".claude")
CFG_PATH = os.path.join(CLAUDE_DIR, "bash-guard.json")
OFF_PATH = os.path.join(CLAUDE_DIR, "bash-guard.off")
LOG_PATH = os.path.join(CLAUDE_DIR, "logs", "bash-guard.jsonl")
SSH_CONFIG = os.path.join(HOME, ".ssh", "config")

MARKER = re.compile(r"#\s*danger-ok:\s*(\S.{2,})")
PROBE = "bash-guard-ask-probe"
LEVELS = ("block", "confirm", "ask")

DEFAULTS = {
    "prod_hosts": [],
    "protected_branches": ["main", "master", "dev"],
    "safe_rm_dirs": ["node_modules", "dist", "build", "tmp", ".cache", "coverage", "__pycache__"],
    "tmp_roots": ["/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/"],
    "rules": [],
}

# Обёртка → её опции, которые забирают следующее слово.
WRAPPERS = {
    "sudo": "-u -g -C -p -h -U", "doas": "-u", "env": "-u -C -S", "nice": "-n",
    "xargs": "-I -n -P -L -d -E -s", "stdbuf": "-i -o -e", "timeout": "-s -k", "gtimeout": "-s -k",
    "exec": "", "nohup": "", "setsid": "", "time": "", "command": "", "builtin": "", "caffeinate": "",
}
SHELLS = frozenset("sh bash zsh dash ksh".split())
SSH_VALUE_FLAGS = frozenset("bcDEeFIiJLlmOoPpQRSWw")  # ssh -p 22, -i key, -o opt…
DOCKER_EXEC_VALUE_FLAGS = frozenset("-u -e -w --user --env --workdir --env-file".split())
FIND_EXEC = ("-exec", "-execdir", "-ok", "-okdir")
TEXT_FLAGS = {"-m", "--message", "-F", "--file", "--body", "-b", "--title", "-t", "--notes", "-n", "--subject"}

ENV_TEMPLATES = (".example", ".sample", ".template")
READERS = {"cat", "less", "more", "head", "tail", "bat", "nl", "xxd", "od", "strings", "sort", "uniq",
           "cut", "awk", "grep", "egrep", "fgrep", "rg", "ag", "source", ".", "diff", "vim", "vi", "nano"}
GREPS = {"grep", "egrep", "fgrep", "rg", "ag"}
GREP_QUIET = {"-c", "--count", "-l", "--files-with-matches", "-L", "--files-without-match", "-q", "--quiet"}
SQL_CLIENTS = {"psql", "sqlite3", "mysql"}
SQL_WRITE = re.compile(
    r"\b(DROP\s+(TABLE|INDEX|SCHEMA|DATABASE|VIEW|SEQUENCE|TYPE|FUNCTION|TRIGGER|COLUMN|CONSTRAINT)"
    r"|TRUNCATE|DELETE\s+FROM|UPDATE\s+[\w.\"]+\s+SET"
    r"|ALTER\s+(TABLE|DATABASE|SCHEMA|INDEX|SEQUENCE|TYPE|ROLE|USER))\b", re.I)
PM2_MUTATING = {"restart", "reload", "stop", "delete", "del", "kill", "start", "scale", "flush", "reset"}
DOCKER_MUTATING = {"restart", "stop", "rm", "kill", "rmi", "down", "pause", "update", "prune", "up"}
SERVICE_MUTATING = {"restart", "stop", "start", "reload", "disable", "enable", "kill", "mask"}

SECRET_MASKS = [
    (re.compile(r"(?i)\b([\w-]*(?:key|token|secret|passw(?:or)?d|pwd|cred)[\w-]*)(\s*[=:]\s*)(['\"]?)[^\s'\"&,;]{4,}"),
     r"\1\2\3***"),
    (re.compile(r"(?i)\bBearer\s+[\w.~+/=-]{8,}"), "Bearer ***"),
    (re.compile(r"(\w+://[^:/\s]+:)[^@\s]+@"), r"\1***@"),
]


# ---------- лексер ----------
# Токен: kind — word | op (разделитель) | redir (следом цель) | herestring (следом текст) | heredoc.
# text — слово без кавычек ($VAR и ~ остаются как есть, их раскрывает Parser.expand),
# subs — команды из $(…) и `…` внутри слова, fd — слово из одних цифр (номер дескриптора).

CONTROL_OPS = ("&&", "||", ";;", "|&", ";", "|", "&", "(", ")", "\n")
REDIR_OPS = ("<<<", "<<-", "&>>", "<<", "&>", ">>", ">&", "<&", ">|", "<>", ">", "<")
WORD_END = frozenset(" \t\r\n;&|()<>")


class Tok:
    __slots__ = ("kind", "text", "subs", "fd")

    def __init__(self, kind, text="", subs=None, fd=False):
        self.kind, self.text, self.subs, self.fd = kind, text, subs or [], fd


def paren_end(src, i):
    """src[i] == '(' от `$(`: (индекс за парной ')', закрыта ли)."""
    depth, quote, k = 0, None, i
    while k < len(src):
        ch = src[k]
        if quote == "'":
            quote = None if ch == "'" else quote
        elif ch == "\\":
            k += 1
        elif quote == '"':
            quote = None if ch == '"' else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return k + 1, True
        k += 1
    return len(src), False


def backtick_end(src, i):
    """src[i] == '`': (индекс за закрывающей '`', закрыта ли)."""
    k = i + 1
    while k < len(src):
        if src[k] == "\\":
            k += 2
        elif src[k] == "`":
            return k + 1, True
        else:
            k += 1
    return len(src), False


def take_substitution(src, i, out, subs):
    """Если с i начинается $(…) или `…` — дописывает его в out как есть, команду — в subs.
    -> новый индекс или None, если подстановки здесь нет."""
    if src.startswith("$(", i):
        end, closed = paren_end(src, i + 1)
        subs.append(src[i + 2:end - 1 if closed else end])
    elif src[i] == "`":
        end, closed = backtick_end(src, i)
        subs.append(src[i + 1:end - 1 if closed else end])
    else:
        return None
    out.append(src[i:end])
    return end


def scan_substitutions(text):
    """Команды из $(…) и `…` в тексте без кавычечной разметки (тело heredoc)."""
    subs, i = [], 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        nxt = take_substitution(text, i, [], subs)
        i = nxt if nxt is not None else i + 1
    return subs


def read_double_quoted(src, i, out, subs):
    """i — сразу за открывающей ". -> индекс за закрывающей."""
    while i < len(src) and src[i] != '"':
        if src[i] == "\\" and i + 1 < len(src):
            nxt = src[i + 1]
            if nxt != "\n":
                out.append(nxt if nxt in '$`"\\' else "\\" + nxt)
            i += 2
            continue
        after = take_substitution(src, i, out, subs)
        if after is None:
            out.append(src[i])
            i += 1
        else:
            i = after
    return i + 1


def read_word(src, i):
    """-> (Tok слова, индекс за ним). Слово кончается на пробеле или знаке оператора вне кавычек."""
    start, out, subs = i, [], []
    while i < len(src) and src[i] not in WORD_END:
        ch = src[i]
        if ch == "\\":
            if src[i + 1:i + 2] != "\n":  # \<перевод строки> — продолжение строки
                out.append(src[i + 1:i + 2])
            i += 2
        elif ch == "'":
            close = src.find("'", i + 1)
            close = len(src) if close < 0 else close
            out.append(src[i + 1:close])
            i = close + 1
        elif ch == '"':
            i = read_double_quoted(src, i + 1, out, subs)
        elif src.startswith("${", i):
            close = src.find("}", i)
            close = len(src) - 1 if close < 0 else close
            out.append(src[i:close + 1])
            i = close + 1
        else:
            after = take_substitution(src, i, out, subs)
            if after is None:
                out.append(ch)
                i += 1
            else:
                i = after
    raw = src[start:i]
    return Tok("word", "".join(out), subs, fd=raw.isdigit()), i


def read_heredoc_bodies(src, i, pending):
    """Тела heredoc идут строками после перевода строки, в порядке объявления. -> индекс за ними."""
    for tok, delim, strip_tabs, live in pending:
        lines = []
        while i < len(src):
            nl = src.find("\n", i)
            nl = len(src) if nl < 0 else nl
            line, i = src[i:nl], nl + 1
            probe = line.lstrip("\t") if strip_tabs else line
            if probe == delim:
                break
            lines.append(line)
        tok.text = "\n".join(lines)
        if live:
            tok.subs = scan_substitutions(tok.text)
    return min(i, len(src))


def first_of(src, i, ops):
    return next((op for op in ops if src.startswith(op, i)), None)


def lex(src):
    toks, pending, i = [], [], 0
    while i < len(src):
        ch = src[i]
        if ch in " \t\r":
            i += 1
        elif src.startswith("\\\n", i):
            i += 2
        elif ch == "#":  # в начале слова — комментарий до конца строки
            nl = src.find("\n", i)
            i = len(src) if nl < 0 else nl
        elif ch == "\n":
            toks.append(Tok("op", "\n"))
            i += 1
            if pending:
                i = read_heredoc_bodies(src, i, pending)
                pending = []
        elif first_of(src, i, REDIR_OPS):
            op = first_of(src, i, REDIR_OPS)
            i += len(op)
            if op in ("<<", "<<-"):
                while i < len(src) and src[i] in " \t":
                    i += 1
                word_start = i
                delim, i = read_word(src, i)
                tok = Tok("heredoc")
                toks.append(tok)
                if delim.text:
                    quoted = any(c in src[word_start:i] for c in "'\"\\")
                    pending.append((tok, delim.text, op == "<<-", not quoted))
            else:
                toks.append(Tok("herestring" if op == "<<<" else "redir", op))
        elif first_of(src, i, CONTROL_OPS):
            op = first_of(src, i, CONTROL_OPS)
            toks.append(Tok("op", op))
            i += len(op)
        else:
            word, i = read_word(src, i)
            if not (word.fd and src[i:i + 1] in ("<", ">")):  # 2>err, 2>&1: номер дескриптора не слово
                toks.append(word)
    return toks


# ---------- разбор в простые команды ----------

class Segment:
    """Простая команда: argv, редиректы [(оператор, цель)], body — текст heredoc или <<<,
    host — хост, если команда пришла через ssh, cwd — каталог, piped_from — команда слева от |."""
    __slots__ = ("argv", "redirs", "body", "host", "cwd", "piped_from")

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields.get(name))


VAR_REF = re.compile(r"\$(?:\{(\w+)\}|(\w+))")


def as_assignment(word):
    """VAR=value -> (VAR, value), иначе None."""
    name, eq, value = word.partition("=")
    return (name, value) if eq and name.isidentifier() else None


class Parser:
    MAX_DEPTH = 6

    def __init__(self, cwd, env):
        self.segments = []
        self.vars = dict(env)
        self.home = env.get("HOME") or HOME
        self.cwd = cwd
        self.depth = 0

    def expand(self, word):
        """~ в начале и $VAR / ${VAR} из известных переменных; неизвестные остаются как есть."""
        if word == "~" or word.startswith("~/"):
            word = self.home + word[1:]
        return VAR_REF.sub(lambda m: self.vars.get(m.group(1) or m.group(2), m.group(0)), word)

    def resolve(self, seg, path):
        return os.path.normpath(os.path.join(seg.cwd, self.expand(path)))

    def parse(self, cmd, host=None):
        if self.depth >= self.MAX_DEPTH or not cmd.strip():
            return
        self.depth += 1
        try:
            self._walk(lex(cmd), host)
        finally:
            self.depth -= 1

    def _walk(self, toks, host):
        argv, redirs, body, upstream = [], [], None, None
        k = 0
        while k < len(toks):
            tok = toks[k]
            used = [tok]
            if tok.kind == "op":
                seg = self.add(argv, redirs, body, host, upstream) if argv or redirs else None
                upstream = seg if tok.text in ("|", "|&") else None
                argv, redirs, body = [], [], None
            elif tok.kind == "word":
                argv.append(tok.text)
            elif tok.kind == "heredoc":
                body = tok.text
            else:  # redir / herestring забирают следующее слово
                nxt = toks[k + 1] if k + 1 < len(toks) and toks[k + 1].kind == "word" else None
                if nxt is not None:
                    used.append(nxt)
                    k += 1
                if tok.kind == "herestring":
                    body = nxt.text if nxt else ""
                else:
                    redirs.append((tok.text, nxt.text if nxt else ""))
            for t in used:
                for inner in t.subs:
                    self.parse(inner, host)
            k += 1
        if argv or redirs:
            self.add(argv, redirs, body, host, upstream)

    def _strip_prefix(self, argv):
        """Снимает VAR=… и обёртки; export только запоминает переменные. None — команды нет."""
        while argv:
            pair = as_assignment(argv[0])
            if pair:
                self.vars[pair[0]] = self.expand(pair[1])
                del argv[0]
                continue
            prog = os.path.basename(argv[0])
            if prog == "export":
                for pair in filter(None, map(as_assignment, argv[1:])):
                    self.vars[pair[0]] = self.expand(pair[1])
                return None
            if prog not in WRAPPERS:
                break
            del argv[0]
            value_opts = WRAPPERS[prog].split()
            while argv and (argv[0].startswith("-") or as_assignment(argv[0])):
                opt = argv.pop(0)
                if opt in value_opts and argv:
                    del argv[0]
            if prog in ("timeout", "gtimeout") and argv:
                del argv[0]  # длительность
        return argv

    def add(self, argv, redirs, body, host, upstream):
        argv = self._strip_prefix(list(argv))
        if argv is None or not (argv or redirs):
            return None
        seg = Segment(argv=argv, redirs=redirs, body=body, host=host, cwd=self.cwd, piped_from=upstream)
        self.segments.append(seg)
        if argv:
            self._follow(seg)
        return seg

    def _follow(self, seg):
        """Что команда меняет для следующих (cd) и что запускает внутри себя."""
        prog, args = os.path.basename(seg.argv[0]), seg.argv[1:]
        if prog in ("cd", "pushd") and seg.host is None:
            if not args:
                self.cwd = self.home
            elif not args[0].startswith("-"):
                self.cwd = os.path.normpath(os.path.join(self.cwd, self.expand(args[0])))
        elif prog in SHELLS:
            self._shell(args, seg.body, seg.host)
        elif prog == "eval":
            self.parse(" ".join(args), seg.host)
        elif prog in ("ssh", "autossh"):
            self._ssh(args, seg.body)
        elif prog == "docker" and args[:1] == ["exec"]:
            self._docker_exec(args[1:], seg)
        elif prog == "find":
            self._find_exec(args, seg.host)

    def _shell(self, args, body, host):
        opts = list(itertools.takewhile(lambda a: a.startswith("-"), args))
        rest = args[len(opts):]
        if any(o[1:2] != "-" and "c" in o for o in opts):
            if rest:
                self.parse(rest[0], host)  # bash -c '…'
        elif not rest and body:
            self.parse(body, host)  # bash <<EOF … EOF; bash script.sh — скрипт не читаем

    def _ssh(self, args, body):
        words = iter(args)
        host = None
        for word in words:
            if not word.startswith("-"):
                host = word
                break
            if len(word) == 2 and word[1] in SSH_VALUE_FLAGS:
                next(words, None)
        if host is None:
            return
        remote = list(words)
        if remote:
            self.parse(" ".join(remote), host)
        runs_shell = not remote or os.path.basename(remote[0]) in SHELLS
        if body and runs_shell:
            self.parse(body, host)  # ssh хост <<EOF, ssh хост bash <<EOF

    def _docker_exec(self, args, seg):
        words = iter(args)
        for word in words:
            if word.startswith("-"):
                if word in DOCKER_EXEC_VALUE_FLAGS:
                    next(words, None)
                continue
            inner = list(words)  # word — контейнер
            if inner:
                self.add(inner, [], seg.body, seg.host, seg.piped_from)
            return

    def _find_exec(self, args, host):
        for k, word in enumerate(args):
            if word in FIND_EXEC:
                inner = list(itertools.takewhile(lambda a: a not in (";", "+"), args[k + 1:]))
                if inner:
                    self.add(inner, [], None, host, None)


# ---------- окружение: git, хосты, файлы ----------

def load_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def git_dir_of(path):
    """(каталог .git этого дерева, корень дерева) без запуска git; worktree — по файлу .git."""
    d = path
    while True:
        g = os.path.join(d, ".git")
        if os.path.isdir(g):
            return g, d
        if os.path.isfile(g):
            try:
                m = re.match(r"gitdir:\s*(.+)", Path(g).read_text(encoding="utf-8").strip())
                if m:
                    gd = m.group(1)
                    return (gd if os.path.isabs(gd) else os.path.normpath(os.path.join(d, gd))), d
            except Exception:
                pass
            return None, d
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        d = parent


def main_tree(gitdir):
    """Корень главного дерева для worktree (по файлу commondir), иначе None."""
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as f:
            common = os.path.normpath(os.path.join(gitdir, f.read().strip()))
        return os.path.dirname(common)
    except Exception:
        return None


def current_branch(path):
    gitdir, _ = git_dir_of(path)
    try:
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
    except Exception:
        return None
    prefix = "ref: refs/heads/"
    return head[len(prefix):] if head.startswith(prefix) else None


def prod_hosts(cfg, ssh_config=SSH_CONFIG):
    """prod_hosts из конфига плюс их алиасы из ~/.ssh/config (Host x → HostName <прод>)."""
    hosts = set(cfg.get("prod_hosts") or [])
    try:
        names = []
        with open(ssh_config, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        for line in lines:
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            key, value = parts[0].lower(), parts[1].strip()
            if key == "host":
                names = value.split()
            elif key == "hostname" and value in hosts:
                hosts.update(h for h in names if not any(ch in h for ch in "*?!"))
    except Exception:
        pass
    return hosts


def bare_host(h):
    return h.split("@", 1)[-1].split(":", 1)[0] if h else h


def is_env_file(path):
    name = os.path.basename(path.rstrip("/"))
    return (name == ".env" or name.startswith(".env.")) and not name.endswith(ENV_TEMPLATES)


def in_tmp(path, cfg, env):
    roots = list(cfg.get("tmp_roots") or [])
    if env.get("TMPDIR"):
        roots.append(env["TMPDIR"])
    p = os.path.realpath(path).rstrip("/") + "/"
    return any(p.startswith(os.path.realpath(r).rstrip("/") + "/") for r in roots)


def rm_target(pr, seg, raw, cfg, env):
    """'root' — корень, дом, репо целиком; 'safe' — временное и сборка; 'other' — остальное."""
    word = pr.expand(raw)
    if raw in ("*", ".", "./", "..", "../", "/", "/*", "~", "~/", "~/*") or word.rstrip("/") in ("", pr.home):
        return "root"
    if re.fullmatch(r"(\.\./)+\.?\.?/?|\.\./\*", raw):
        return "root"
    if "$" in word:
        return "other"  # переменная не раскрылась — что удалит, неизвестно
    path = pr.resolve(seg, word)
    base = (path[:-2] if path.endswith("/*") else path).rstrip("/")
    if base in ("", pr.home) or pr.home.startswith(base + "/"):
        return "root"
    if os.path.exists(os.path.join(base, ".git")):
        return "root"
    if in_tmp(base, cfg, env):
        return "safe"
    # Безопасной цель делает её собственный путь (node_modules, src/__pycache__), а не каталог,
    # где стоит сессия: иначе любой rm -r внутри …/tmp/… проходил бы молча.
    typed = word[len(pr.home) + 1:] if word.startswith(pr.home + "/") else word  # ~/x → x
    if set(typed.rstrip("/").split("/")) & set(cfg.get("safe_rm_dirs") or []):
        return "safe"
    return "other"


def git_subcommand(argv):
    """git -C dir push … → ('push', [аргументы], 'dir')."""
    i, cdir = 1, None
    while i < len(argv):
        a = argv[i]
        if a == "-C" and i + 1 < len(argv):
            cdir = argv[i + 1]
            i += 2
        elif a in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(argv):
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return a, argv[i + 1:], cdir
    return None, [], cdir


def has_short_flag(args, letter):
    return any(re.fullmatch(r"-[a-zA-Z]+", a) and letter in a for a in args)


# ---------- встроенные правила ----------

def check(pr, seg, cfg, hosts, env, hit):
    argv = seg.argv
    name = os.path.basename(argv[0]) if argv else ""
    args = argv[1:]
    pos = [a for a in args if not a.startswith("-")]
    remote = seg.host is not None
    host = bare_host(seg.host)
    on_prod = remote and host in hosts

    # .env: запись. Перезаписать существующий файл секретов — блок; на сервере — любая запись.
    def env_write(target, how):
        if not is_env_file(target):
            return
        if remote:
            hit("block", "env-write-remote", f"{how} {target} на сервере {host}")
        elif os.path.exists(pr.resolve(seg, target)):
            hit("block", "env-overwrite", f"{how} поверх существующего {target}")

    for op, target in seg.redirs:
        if ">" in op and target:
            env_write(target, "запись в")
    if not argv:
        return
    if name in ("cp", "mv", "install", "ln") and len(pos) >= 2:
        env_write(pos[-1], name)
    if name == "tee":
        for p in pos:
            env_write(p, "tee в")
    if name in ("sed", "perl") and any(a.startswith(("-i", "-pi")) for a in args):
        for p in pos:
            env_write(p, f"{name} -i по")
    if name in ("scp", "rsync") and pos and ":" in pos[-1] and is_env_file(pos[-1].split(":", 1)[1]):
        hit("block", "env-write-remote", f"{name} в {pos[-1]}")

    # .env: чтение — секреты уходят в контекст модели и в транскрипт.
    if name in READERS:
        quiet = name in GREPS and any(a in GREP_QUIET or has_short_flag([a], "c") or has_short_flag([a], "l")
                                      or has_short_flag([a], "q") for a in args)
        if not quiet and any(is_env_file(p) for p in pos):
            where = f" на сервере {host}" if remote else ""
            hit("confirm", "env-read", f"{name} читает .env{where}: секреты попадут в контекст")

    if name == "echo" and PROBE in args:
        hit("ask", "ask-probe", "проба окна подтверждения (безвредная)")

    if name == "rm":
        check_rm(pr, seg, args, cfg, env, remote, on_prod, host, hit)
    if name == "git":
        check_git(pr, seg, argv, cfg, env, hit)

    if name == "pm2" and pos and pos[0] in PM2_MUTATING:
        targets = pos[1:]
        if pos[0] == "kill" or any(t == "all" or t.isdigit() for t in targets):
            hit("block", "pm2-all-or-id", f"pm2 {pos[0]} {' '.join(targets)}: только по имени процесса")
        elif on_prod:
            hit("ask", "pm2-prod", f"pm2 {pos[0]} {' '.join(targets)} на {host}")
    if on_prod and name in ("docker", "docker-compose"):
        verb = pos[1] if name == "docker" and pos[:1] == ["compose"] and len(pos) > 1 else (pos[0] if pos else "")
        if verb in DOCKER_MUTATING:
            hit("ask", "docker-prod", f"docker {verb} на {host}")
    if on_prod and name in ("systemctl", "service") and set(pos) & SERVICE_MUTATING:
        hit("ask", "service-prod", f"{name} {' '.join(pos)} на {host}")

    if name in SQL_CLIENTS:
        check_sql(seg, args, pos, name, remote, on_prod, host, hit)

    # curl … | sh: чужой скрипт исполняется не глядя.
    if name in SHELLS | {"python", "python3", "node", "perl", "ruby"} and not pos:
        src = seg.piped_from
        while src is not None:
            if src.argv and os.path.basename(src.argv[0]) in ("curl", "wget"):
                hit("confirm", "curl-pipe-sh", f"{os.path.basename(src.argv[0])} … | {name}")
                break
            src = src.piped_from


def check_rm(pr, seg, args, cfg, env, remote, on_prod, host, hit):
    flags = [a for a in args if a.startswith("-") and a != "--"]
    recursive = any(a in ("--recursive", "-R") or (not a.startswith("--") and set(a[1:]) & {"r", "R"})
                    for a in flags)
    if not recursive:
        return
    targets = args[args.index("--") + 1:] if "--" in args else [a for a in args if not a.startswith("-")]
    shown = " ".join(targets)[:80]
    if remote:
        hit("ask" if on_prod else "confirm", "rm-remote", f"rm -r на {host}: {shown}")
        return
    kinds = [rm_target(pr, seg, t, cfg, env) for t in targets] or ["other"]
    if "root" in kinds:
        hit("block", "rm-root", f"rm -r по корню, дому или репо целиком: {shown}")
    elif "other" in kinds:
        hit("confirm", "rm-r", f"rm -r вне временных каталогов и сборки: {shown}")


def check_git(pr, seg, argv, cfg, env, hit):
    sub, rest, cdir = git_subcommand(argv)
    if sub is None:
        return
    protected = set(cfg.get("protected_branches") or [])
    where = pr.resolve(seg, cdir) if cdir else seg.cwd
    if sub == "push":
        force = "--force" in rest or has_short_flag(rest, "f")
        lease = any(a.startswith("--force-with-lease") for a in rest)
        delete = "--delete" in rest or "-d" in rest
        refs = [a for a in rest if not a.startswith("-")][1:]  # первый — remote
        dests = [r.lstrip("+").split(":")[-1].replace("refs/heads/", "") for r in refs]
        deleted = {r[1:] for r in refs if r.startswith(":")} | (set(dests) if delete else set())
        if not dests and seg.host is None:
            branch = current_branch(where)
            dests = [branch] if branch else []
        if force or any(r.startswith("+") for r in refs):
            hit("block", "push-force", "git push --force перезаписывает чужую историю; нужно — --force-with-lease и не в защищённые ветки")
        elif lease and set(dests) & protected:
            hit("block", "push-lease-protected", f"--force-with-lease в {', '.join(sorted(set(dests) & protected))}")
        if deleted & protected:
            hit("block", "push-delete-protected", f"удаление ветки {', '.join(sorted(deleted & protected))}")
        return
    # Репо во временном каталоге (песочница тестов, scratchpad) — разрушать там нечего.
    if seg.host is None and in_tmp(where, cfg, env):
        return
    if sub == "reset" and "--hard" in rest:
        hit("confirm", "git-reset-hard", "git reset --hard стирает незакоммиченное")
    elif sub == "clean" and ("--force" in rest or has_short_flag(rest, "f")):
        hit("confirm", "git-clean", "git clean -f удаляет неотслеживаемые файлы")
    elif sub == "branch" and has_short_flag(rest, "D"):
        hit("confirm", "git-branch-D", "git branch -D удаляет ветку без проверки слияния")
    elif sub == "worktree" and rest[:1] == ["remove"]:
        target = [a for a in rest[1:] if not a.startswith("-")]
        if not target or rm_target(pr, seg, target[0], cfg, env) != "safe":
            hit("confirm", "git-worktree-remove", f"git worktree remove {' '.join(target)}: дерево может быть чужой сессии")


def check_sql(seg, args, pos, name, remote, on_prod, host, hit):
    sql = []
    for k, a in enumerate(args):
        if a in ("-c", "--command", "-e", "--execute") and k + 1 < len(args):
            sql.append(args[k + 1])
        elif a.startswith("--command="):
            sql.append(a.split("=", 1)[1])
    if name == "sqlite3" and len(pos) >= 2:
        sql.append(" ".join(pos[1:]))
    if seg.body:
        sql.append(seg.body)
    src = seg.piped_from
    while src is not None:  # echo "DELETE …" | psql
        sql.append(" ".join(src.argv))
        if src.body:
            sql.append(src.body)
        src = src.piped_from
    m = SQL_WRITE.search("\n".join(sql))
    if not m:
        return
    verb = m.group(1).split()[0].upper()
    if on_prod:
        hit("ask", "sql-write-prod", f"SQL {verb} на {host}")
    else:
        hit("confirm", "sql-write", f"SQL {verb}" + (f" на {host}" if remote else ""))


# ---------- правила из конфига ----------

def text_free_view(argv):
    """argv без текстов коммитов и PR: слово в сообщении коммита не должно срабатывать как команда."""
    name = os.path.basename(argv[0]) if argv else ""
    if name in ("echo", "printf"):
        return None
    if name not in ("git", "gh"):
        return " ".join(argv)
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a in TEXT_FLAGS:
            skip = True
        elif not re.match(r"^--(message|body|title|notes|subject)=", a):
            out.append(a)
    return " ".join(out)


def config_rules(cfg, pr, cwd, env):
    """Правила глобального конфига + .claude/bash-guard.json корня репо (и главного дерева для worktree)."""
    rules = list(cfg.get("rules") or [])
    seen = set()
    for d in {cwd, pr.cwd, env.get("CLAUDE_PROJECT_DIR") or ""} | {s.cwd for s in pr.segments}:
        if not d:
            continue
        gitdir, top = git_dir_of(d)
        for root in (top, main_tree(gitdir) if gitdir else None):
            if root and root not in seen:
                seen.add(root)
                rules += (load_json(os.path.join(root, ".claude", "bash-guard.json")) or {}).get("rules") or []
    compiled = []
    for r in rules:
        try:
            if r.get("level") in LEVELS and r.get("match"):
                compiled.append((r["level"], r.get("id") or r["match"][:30], r.get("msg") or "", re.compile(r["match"])))
        except (re.error, AttributeError, TypeError):
            pass  # одно битое правило не отключает остальные
    return compiled


# ---------- решение ----------

def load_config(path=CFG_PATH):
    cfg = dict(DEFAULTS)
    cfg.update(load_json(path) or {})
    return cfg


def decide(cmd, cwd, cfg=None, env=None, ssh_config=SSH_CONFIG):
    """-> (решение, [(уровень, id, текст)], цитата из маркера). Решение: allow | block | confirm | marker | ask."""
    cfg = cfg if cfg is not None else load_config()
    env = env if env is not None else dict(os.environ)
    pr = Parser(cwd or HOME, env)
    pr.parse(cmd)
    hits = []
    hosts = prod_hosts(cfg, ssh_config)
    for seg in pr.segments:
        check(pr, seg, cfg, hosts, env, lambda level, rid, msg: hits.append((level, rid, msg)))
    rules = config_rules(cfg, pr, cwd, env)
    for seg in pr.segments:
        view = text_free_view(seg.argv)
        if view is None:
            continue
        for level, rid, msg, rx in rules:
            if rx.search(view):
                hits.append((level, rid, msg or f"правило {rid}"))
    m = MARKER.search(cmd)
    quote = m.group(1).strip() if m else None
    levels = {h[0] for h in hits}
    if "block" in levels:
        return "block", hits, quote
    if "confirm" in levels:
        return ("marker" if quote else "confirm"), hits, quote
    if "ask" in levels:
        return "ask", hits, quote
    return "allow", hits, quote


def masked(s):
    for rx, repl in SECRET_MASKS:
        s = rx.sub(repl, s)
    return s


def write_log(rec, path=LOG_PATH):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def reply(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason,
    }}, ensure_ascii=False))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    cwd = data.get("cwd") or os.getcwd()
    if not cmd or os.path.exists(OFF_PATH):
        return
    try:
        verdict, hits, quote = decide(cmd, cwd)
    except Exception:
        return  # fail-open
    if verdict == "allow":
        return
    write_log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": data.get("session_id"), "cwd": cwd,
               "decision": verdict, "rules": [h[1] for h in hits], "quote": quote, "cmd": masked(cmd)[:600]})
    top = next(h for level in ("block", "confirm", "ask") for h in hits if h[0] == level)
    if verdict == "block":
        reply("deny", f"bash-guard [{top[1]}]: {top[2]}. Блок маркером не снимается: если это нужно, "
                      f"человек делает руками или временно выключает страж (touch ~/.claude/bash-guard.off).")
    elif verdict == "confirm":
        reply("deny", f"bash-guard [{top[1]}]: {top[2]}. Если человек дал согласие именно на это, повтори команду "
                      f"и допиши в конец ` #danger-ok: <его дословная цитата>`. Согласия нет — спроси, "
                      f"не обходи другой формой команды.")
    elif verdict == "ask":
        reply("ask", f"bash-guard [{top[1]}]: {top[2]}. Нужно подтверждение человека.")
    # marker: пропуск, след уже в логе


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
