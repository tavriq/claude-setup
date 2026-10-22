#!/usr/bin/env bash
# UserPromptSubmit hook: предупреждает, когда АКТУАЛЬНЫЙ контекст (то, что реально
# ушло в модель на последнем ходе) разросся.
#
# Раньше мерили size(транскрипта)/4 — это накопленный объём за всю сессию.
# Транскрипт .jsonl только растёт, после /compact старые записи никуда не деваются,
# поэтому число было бессмысленно большим (десятки тысяч k при окне 1M).
#
# Теперь берём usage последней НЕ-sidechain и НЕ-error assistant-записи с конца
# транскрипта (читаем только хвост файла, не весь файл в память) —
# input_tokens + cache_read_input_tokens + cache_creation_input_tokens. Это ровно
# то, что было в окне на последнем ответе модели, и после /compact оно честно падает.
# Если самое свежее событие в хвосте — это граница компакта (compact_boundary) без
# последующего assistant-usage (компакт только что случился, новых ходов ещё не
# было), берём compactMetadata.postTokens из неё — размер сразу после компакта.
# Без этого пришлось бы либо молчать, либо врать старым (пред-компактным) числом.
THRESHOLD_TOKENS="${CONTEXT_WARN_THRESHOLD:-300000}"
WINDOW_TOKENS="${CONTEXT_WARN_WINDOW:-1000000}"

# stdin = JSON харнесса с transcript_path
input="$(cat 2>/dev/null)"
transcript="$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("transcript_path", ""))
except Exception:
    print("")
' 2>/dev/null)"

[ -z "$transcript" ] && exit 0
[ -f "$transcript" ] || exit 0

# Хвоста в 500 строк с запасом хватает, чтобы найти последний assistant-usage
# (или границу компакта), не читая весь (потенциально десятки МБ) файл.
tokens="$(tail -n 500 "$transcript" 2>/dev/null | python3 -c '
import json, sys

tokens = None
for line in reversed(sys.stdin.readlines()):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue

    t = d.get("type")

    if t == "assistant":
        if d.get("isSidechain") or d.get("isApiErrorMessage"):
            continue
        usage = (d.get("message") or {}).get("usage") or {}
        if not usage:
            continue
        tokens = (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        )
        break

    if t == "system" and d.get("subtype") == "compact_boundary":
        post = (d.get("compactMetadata") or {}).get("postTokens")
        if isinstance(post, (int, float)):
            tokens = int(post)
        break

if tokens is not None:
    print(tokens)
' 2>/dev/null)"

[ -z "$tokens" ] && exit 0
case "$tokens" in (*[!0-9]*|'') exit 0 ;; esac

if [ "$tokens" -ge "$THRESHOLD_TOKENS" ]; then
  k=$(( tokens / 1000 ))
  pct=$(( tokens * 100 / WINDOW_TOKENS ))
  msg="🟡 Контекст разросся (~${k}k tok, ${pct}% окна, порог $(( THRESHOLD_TOKENS / 1000 ))k). Несвязанная задача → /clear. Та же задача, длинный тред → /compact."
  # systemMessage видит человек, в контекст модели он не уходит
  printf '{"systemMessage": %s}\n' "$(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
fi
exit 0
