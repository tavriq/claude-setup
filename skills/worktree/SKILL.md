---
name: worktree
description: Git worktrees — создание/управление, conventional naming `feat/<name>` от базовой ветки (автодетект dev→main→master). Когда нужна параллельная работа, изоляция изменений или эксперимент с откатом.
---

# Git Worktree Manager

Управление изолированными рабочими копиями через `git worktree`. Позволяет нескольким Claude работать параллельно в одном проекте, не мешая друг другу.

## Когда активировать

- «создай worktree», «работай в отдельной ветке», «сделай это изолированно»
- «параллельно сделай X и Y», «запусти это отдельно»
- «попробуй вот это, но чтобы можно было откатить»
- «смержи worktree», «удали worktree», «список worktrees»

## Конвенция веток

**Префикс:** `feat/<name>` (conventional naming, стандарт для PR-флоу).

**Базовая ветка (от чего ответвлять):** автодетект в этом порядке:
1. `dev` (если существует)
2. `main` (если существует)
3. `master` (если существует)
4. Текущая ветка (если выше не нашлось)

```bash
BASE=$(git show-ref --verify --quiet refs/heads/dev && echo dev \
  || git show-ref --verify --quiet refs/heads/main && echo main \
  || git show-ref --verify --quiet refs/heads/master && echo master \
  || git branch --show-current)
```

## Операции

### 1. Создать worktree

**Аргумент:** `$ARGUMENTS` = имя worktree (например: `chat-screen`, `auth-refactor`, `experiment-fcm`).

```bash
# Убедиться что .claude/worktrees/ в .gitignore
grep -q '.claude/worktrees/' .gitignore 2>/dev/null || echo '.claude/worktrees/' >> .gitignore

# Определить базовую ветку
BASE=$(git show-ref --verify --quiet refs/heads/dev && echo dev \
  || git show-ref --verify --quiet refs/heads/main && echo main \
  || git show-ref --verify --quiet refs/heads/master && echo master \
  || git branch --show-current)

# Подтянуть свежий base, если есть upstream
git fetch origin "$BASE" 2>/dev/null || true

# Создать worktree с веткой feat/<name> от base
git worktree add .claude/worktrees/$ARGUMENTS -b feat/$ARGUMENTS "$BASE"
```

После создания сообщи {{USER_NAME}}:
```
Worktree "$ARGUMENTS" создан:
- Папка: .claude/worktrees/$ARGUMENTS/
- Ветка: feat/$ARGUMENTS (от $BASE)
- Все мои действия теперь будут в этой папке.

Когда закончу — скажи «смержи worktree $ARGUMENTS» чтобы влить через PR в $BASE.
```

**После создания — ПЕРЕКЛЮЧИСЬ на работу в worktree:**
- ВСЕ операции с файлами делай через путь `.claude/worktrees/$ARGUMENTS/`
- Читай файлы: `.claude/worktrees/$ARGUMENTS/path/to/file`
- Пиши файлы: `.claude/worktrees/$ARGUMENTS/path/to/file`
- Запускай скрипты: `cd .claude/worktrees/$ARGUMENTS && <command>`
- НЕ трогай файлы в корне проекта

### 2. Показать все worktrees

```bash
git worktree list
```

### 3. Смержить worktree (через PR — preferred)

**Аргумент:** `$ARGUMENTS` = имя worktree.

```bash
# 1. Закоммитить незакоммиченные изменения в worktree
cd .claude/worktrees/$ARGUMENTS && git add -A && git status
```

Если есть что коммитить:
```bash
cd .claude/worktrees/$ARGUMENTS && git commit -m "feat(<scope>): описание изменений worktree $ARGUMENTS"
```

```bash
# 2. Запушить ветку и открыть PR (если есть remote)
cd .claude/worktrees/$ARGUMENTS && git push -u origin feat/$ARGUMENTS
```

Если есть `gh` CLI и remote — открой PR:
```bash
cd .claude/worktrees/$ARGUMENTS && gh pr create --base $BASE --title "feat(<scope>): worktree $ARGUMENTS" --body "..."
```

**Прямой merge без PR — только если {{USER_NAME}} явно попросил «смержи без PR»:**
```bash
cd <корень_проекта> && git merge feat/$ARGUMENTS --no-edit
```

После успешного merge/PR спроси: «Удалить worktree $ARGUMENTS? Изменения уже в $BASE (или ждут review в PR).»

### 4. Удалить worktree

**Аргумент:** `$ARGUMENTS` = имя worktree.

```bash
git worktree remove .claude/worktrees/$ARGUMENTS
git branch -d feat/$ARGUMENTS    # -D если ветка ещё не смержена и {{USER_NAME}} подтвердил
```

## Правила работы в worktree

1. **Все пути** — через `.claude/worktrees/<name>/`, НИКОГДА через корень
2. **Скрипты** — `cd .claude/worktrees/<name> && <command>`
3. **Результаты** — в `.claude/worktrees/<name>/tmp/...`
4. **Коммиты часто** — маленькие коммиты = чистый merge
5. **Не трогай корень** — только свой worktree
6. **CLAUDE.md и skills** — читаются из корня проекта (общие для всех)
7. **node_modules / ios-pods / venv** — есть только в корне; в worktree их нет. Если нужно собрать/протестить — либо ставь в worktree (`npm install --legacy-peer-deps` если RN-проект), либо сделай symlink

## Обработка ошибок

| Ошибка | Решение |
|--------|---------|
| `fatal: is not a git repository` | Проект не git — выполни `git init` |
| `fatal: 'feat/X' is already checked out` | Worktree с таким именем уже есть — `git worktree list` |
| `fatal: cannot create branch... already exists` | Ветка `feat/<name>` уже существует — либо переиспользуй (без `-b`, с явным `feat/<name>`), либо выбери другое имя |
| `error: branch 'feat/X' not found` | Ветка уже удалена — просто удали папку: `rm -rf .claude/worktrees/X` |
| Мерж-конфликт | Покажи конфликтующие файлы, помоги разрешить, `git add` + `git commit` |
