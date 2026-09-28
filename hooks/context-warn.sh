#!/usr/bin/env bash
# UserPromptSubmit hook: предупреждает, когда транскрипт сессии разросся.
# Грубая прокси размера контекста = размер jsonl-транскрипта. Это ВЕРХНЯЯ
# оценка (включает tool-выводы, которые харнесс мог обрезать/закешировать),
# но как сигнал «пора /compact или /clear» работает надёжно.
#
# Порог в токенах (~80k по умолчанию). Меняется через CONTEXT_WARN_THRESHOLD. chars/4 ≈ tokens.
THRESHOLD_TOKENS="${CONTEXT_WARN_THRESHOLD:-80000}"

# stdin = JSON харнесса с transcript_path
input="$(cat)"
transcript="$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("transcript_path",""))' 2>/dev/null)"

[ -z "$transcript" ] || [ ! -f "$transcript" ] && exit 0

chars="$(wc -c < "$transcript" 2>/dev/null | tr -d ' ')"
[ -z "$chars" ] && exit 0
tokens=$(( chars / 4 ))

if [ "$tokens" -ge "$THRESHOLD_TOKENS" ]; then
  k=$(( tokens / 1000 ))
  msg="🟡 Контекст разросся (~${k}k tok, порог $(( THRESHOLD_TOKENS / 1000 ))k). Несвязанная задача → /clear. Та же задача, длинный тред → /compact."
  # systemMessage показывается пользователю, НЕ уходит в контекст Claude
  printf '{"systemMessage": %s}\n' "$(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
fi
exit 0
