#!/usr/bin/env bash
# Заводит worktree под задачу: <корень репо>/.claude/worktrees/<имя>, ветка feat/<имя>.
#
#   new-worktree.sh <имя>
#
# База: dev, если есть, иначе main, иначе master (локальная ветка или origin/<ветка>);
# нет ни одной — текущая ветка. Есть origin: сначала git fetch, ветка идёт от свежего
# origin/<база>, без upstream (иначе голый git push ушёл бы в базу). Нет origin — от локальной.
set -euo pipefail

name="${1:-}"
[ -n "$name" ] || { echo "использование: new-worktree.sh <имя>" >&2; exit 2; }
branch="feat/$name"
git check-ref-format --branch "$branch" >/dev/null 2>&1 \
  || { echo "имя не годится для ветки: $branch" >&2; exit 2; }

# Корень главного дерева: первая строка списка worktree, откуда бы ни звали.
root="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
[ -n "$root" ] || { echo "не git-репо: $(pwd)" >&2; exit 1; }
cd "$root"

if git remote get-url origin >/dev/null 2>&1; then
  git fetch --quiet origin || echo "git fetch origin не прошёл: беру то, что есть локально" >&2
fi

has_ref() { git show-ref --verify --quiet "$1"; }

base=""
for b in dev main master; do
  if has_ref "refs/heads/$b" || has_ref "refs/remotes/origin/$b"; then
    base="$b"
    break
  fi
done
if [ -z "$base" ]; then
  base="$(git branch --show-current)"
  [ -n "$base" ] || { echo "нет dev/main/master и HEAD не на ветке: не от чего ответвляться" >&2; exit 1; }
  echo "нет dev/main/master: база — текущая ветка $base" >&2
fi

start="$base"
has_ref "refs/remotes/origin/$base" && start="origin/$base"

# Деревья внутри репо не должны светиться в git status.
if ! grep -qF '.claude/worktrees/' .gitignore 2>/dev/null; then
  [ -s .gitignore ] && [ -n "$(tail -c 1 .gitignore)" ] && echo >> .gitignore
  echo '.claude/worktrees/' >> .gitignore
fi

path="$root/.claude/worktrees/$name"
git worktree add --quiet --no-track -b "$branch" "$path" "$start"

echo "worktree: $path"
echo "ветка:    $branch"
echo "база:     $base (от $start, $(git rev-parse --short "$start"))"
