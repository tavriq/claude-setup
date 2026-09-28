#!/usr/bin/env bash
# Устанавливает это окружение Claude Code в ~/.claude.
# Всё, что перезаписывается, сначала уезжает в бэкап-папку с таймстампом.
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

echo "Устанавливаю в $DEST (имя: $NAME)"
[ "$DRY" = 1 ] && echo "РЕЖИМ DRY-RUN — ничего не меняю"

run "mkdir -p '$DEST/agents' '$DEST/skills' '$DEST/hooks'"

# --- CLAUDE.md: подставляем имя вместо плейсхолдера ---
backup_one "$DEST/CLAUDE.md"
if [ "$DRY" = 1 ]; then
  say "[dry-run] CLAUDE.md → $DEST/CLAUDE.md (с подстановкой имени)"
else
  sed "s/{{USER_NAME}}/$NAME/g" "$SRC/CLAUDE.md" > "$DEST/CLAUDE.md"
fi
say "CLAUDE.md установлен"

# --- агенты ---
for f in "$SRC"/agents/*.md; do
  b="$(basename "$f")"
  backup_one "$DEST/agents/$b"
  run "cp '$f' '$DEST/agents/$b'"
done
say "агенты: $(ls -1 "$SRC"/agents/*.md | wc -l | tr -d ' ') шт"

# --- скиллы (с подстановкой имени в SKILL.md) ---
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

# --- хуки ---
for f in "$SRC"/hooks/*; do
  b="$(basename "$f")"
  backup_one "$DEST/hooks/$b"
  run "cp '$f' '$DEST/hooks/$b'"
  run "chmod +x '$DEST/hooks/$b'"
done
say "хуки установлены"

# --- settings.json: не затираем молча ---
if [ -f "$DEST/settings.json" ]; then
  backup_one "$DEST/settings.json"
  if [ "$DRY" = 0 ]; then
    cp "$SRC/settings.json" "$DEST/settings.json.from-setup"
  fi
  say "settings.json уже есть — НЕ трогал. Новый лежит рядом: settings.json.from-setup"
  say "  смёржь руками (hooks/model/effortLevel), старый в бэкапе"
else
  run "cp '$SRC/settings.json' '$DEST/settings.json'"
  say "settings.json установлен"
fi

if [ "$DRY" = 0 ] && [ -d "$BACKUP" ]; then
  echo "Бэкап того, что перезаписал: $BACKUP"
fi
echo "Готово. Запусти 'claude' и проверь: /skills и /agents должны показать новое."
