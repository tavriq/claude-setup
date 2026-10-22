#!/usr/bin/env bash
# Устанавливает это окружение Claude Code в ~/.claude.
# Сначала прогоняет тесты: сломанный страж не ставится. Всё, что перезаписывается,
# сначала уезжает в бэкап-папку с таймстампом.
#
#   ./install.sh                 # спросит имя интерактивно
#   ./install.sh --name Иван     # без вопросов
#   ./install.sh --dry-run       # показать, что будет сделано, ничего не трогая
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}"
BACKUP="$DEST/backups/setup-$(date +%Y%m%d-%H%M%S)"
NAME=""
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="${2:-}"; shift 2 ;;
    --name=*) NAME="${1#*=}"; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "неизвестный аргумент: $1" >&2; exit 1 ;;
  esac
done

command -v python3 >/dev/null || { echo "нужен python3: на нём страж команд и cc-who" >&2; exit 1; }

echo "Тесты стража, cc-who и линтера хуков…"
if ! (cd "$SRC" && python3 -m unittest discover -s tests >/tmp/claude-setup-tests.log 2>&1); then
  echo "тесты упали — не ставлю. Лог: /tmp/claude-setup-tests.log" >&2
  exit 1
fi
say_tests="$(tail -n 3 /tmp/claude-setup-tests.log | grep -E '^Ran ' || true)"
echo "  ${say_tests:-ок}"

if [ -z "$NAME" ]; then
  printf 'Как Claude должен к тебе обращаться? (например: Иван) > '
  read -r NAME
fi
[ -n "$NAME" ] || { echo "имя обязательно — оно подставляется в CLAUDE.md" >&2; exit 1; }

say() { printf '  %s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "[dry-run] $*"; else eval "$@"; fi; }

# бэкапим только то, что реально будет затронуто
backup_one() {
  local target="$1"
  [ -e "$target" ] || return 0
  local rel="${target#$DEST/}"
  if [ "$DRY" = 1 ]; then say "[dry-run] бэкап $rel → $BACKUP/$rel"; return 0; fi
  mkdir -p "$BACKUP/$(dirname "$rel")"
  cp -R "$target" "$BACKUP/$rel"
}

# копия с подстановкой имени вместо {{USER_NAME}}
put_named() {
  local from="$1" to="$2"
  backup_one "$to"
  if [ "$DRY" = 1 ]; then say "[dry-run] $(basename "$from") → $to (с подстановкой имени)"; return 0; fi
  sed "s/{{USER_NAME}}/$NAME/g" "$from" > "$to"
}

echo "Устанавливаю в $DEST (имя: $NAME)"
[ "$DRY" = 1 ] && echo "РЕЖИМ DRY-RUN — ничего не меняю"

run "mkdir -p '$DEST/agents' '$DEST/skills' '$DEST/hooks' '$DEST/bin' '$DEST/rules' '$DEST/roles' '$DEST/handoff'"

put_named "$SRC/CLAUDE.md" "$DEST/CLAUDE.md"
say "CLAUDE.md"

for f in "$SRC"/rules/*.md; do
  put_named "$f" "$DEST/rules/$(basename "$f")"
done
say "правила: $(ls -1 "$SRC"/rules/*.md | wc -l | tr -d ' ') шт"

for f in "$SRC"/roles/*.md; do
  b="$(basename "$f")"
  backup_one "$DEST/roles/$b"
  run "cp '$f' '$DEST/roles/$b'"
done
say "шаблоны ролей: $(ls -1 "$SRC"/roles/*.md | wc -l | tr -d ' ') шт"

for f in "$SRC"/agents/*.md; do
  b="$(basename "$f")"
  backup_one "$DEST/agents/$b"
  run "cp '$f' '$DEST/agents/$b'"
done
say "агенты: $(ls -1 "$SRC"/agents/*.md | wc -l | tr -d ' ') шт"

for d in "$SRC"/skills/*/; do
  s="$(basename "$d")"
  backup_one "$DEST/skills/$s"
  run "rm -rf '$DEST/skills/$s'"
  run "cp -R '$d' '$DEST/skills/$s'"
  if [ "$DRY" = 0 ]; then
    while IFS= read -r md; do
      sed -i.bak "s/{{USER_NAME}}/$NAME/g" "$md" && rm -f "$md.bak"
    done < <(find "$DEST/skills/$s" -name '*.md' -type f)
  fi
done
say "скиллы: $(ls -1d "$SRC"/skills/*/ | wc -l | tr -d ' ') шт"

for f in "$SRC"/hooks/* "$SRC"/bin/*; do
  [ -f "$f" ] || continue
  sub="$(basename "$(dirname "$f")")"
  b="$(basename "$f")"
  backup_one "$DEST/$sub/$b"
  run "cp '$f' '$DEST/$sub/$b'"
  run "chmod +x '$DEST/$sub/$b'"
done
say "хуки: bash-guard, context-warn; bin: cc-who, check-hooks-config"

# --- конфиг стража: свой не затираем ---
if [ -f "$DEST/bash-guard.json" ]; then
  say "bash-guard.json уже есть — не трогал (образец: $SRC/bash-guard.example.json)"
else
  run "cp '$SRC/bash-guard.example.json' '$DEST/bash-guard.json'"
  say "bash-guard.json из образца — впиши свои прод-хосты в prod_hosts"
fi

# --- settings.json: не затираем молча ---
if [ -f "$DEST/settings.json" ]; then
  backup_one "$DEST/settings.json"
  if [ "$DRY" = 0 ]; then
    cp "$SRC/settings.json" "$DEST/settings.json.from-setup"
  fi
  say "settings.json уже есть — НЕ трогал. Новый лежит рядом: settings.json.from-setup"
  say "  перенеси руками блок hooks (страж, cc-who, чистка хвостов), старый в бэкапе"
else
  run "cp '$SRC/settings.json' '$DEST/settings.json'"
  say "settings.json установлен"
fi

if [ "$DRY" = 0 ] && [ -d "$BACKUP" ]; then
  echo "Бэкап того, что перезаписал: $BACKUP"
fi
echo "Готово. Проверка: в новой сессии 'echo bash-guard-ask-probe' должен открыть окно подтверждения,"
echo "'~/.claude/bin/cc-who' — показать таблицу сессий."
