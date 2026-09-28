---
name: install-mcp
disable-model-invocation: true
description: Используй когда нужно установить, удалить, настроить или отладить MCP сервер для Claude Code. Содержит правильный формат команды claude mcp add, типичные ошибки и решения.
---

# Установка MCP серверов для Claude Code

## Требования

- **OS:** macOS / Linux
- **Node:** v20+ с npx
- **Claude CLI:** установлен и доступен в PATH
- **Конфиг MCP:** `~/.claude.json` — секция `mcpServers`

---

## Правильный формат команды

```bash
claude mcp add --transport stdio --env KEY=value <имя_сервера> -- npx -y @namespace/package
```

### Критические правила:
1. **Все флаги ДО имени сервера:** `--transport`, `--env`, `--scope`
2. **`--` отделяет команду запуска** от аргументов Claude CLI
3. **Всегда `-y` с npx** — иначе npx ждёт подтверждения и stdio зависает
4. **Scope по умолчанию = local** (в `~/.claude.json`)

---

## Примеры установки

### Playwright (браузер)
```bash
claude mcp add --transport stdio playwright -- npx -y @playwright/mcp@latest --output-dir tmp/.playwright-mcp
```

### GitHub
```bash
claude mcp add --transport stdio --env GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxx github -- npx -y @modelcontextprotocol/server-github
```

### Context7 (документация)
```bash
claude mcp add --transport stdio --env CONTEXT7_API_KEY=ctx7sk-xxx context7 -- npx -y @upstash/context7-mcp@latest
```

### n8n
```bash
claude mcp add --transport stdio --env N8N_API_URL=https://xxx --env N8N_API_KEY=xxx --env MCP_MODE=stdio --env LOG_LEVEL=error n8n -- npx -y n8n-mcp
```

### HTTP-сервер (без npx)
```bash
claude mcp add --transport http <имя> <url>
```

---

## Scope: куда сохраняется

| Флаг | Файл | Когда использовать |
|------|------|-------------------|
| (по умолчанию / `--scope local`) | `~/.claude.json` | Личный сервер |
| `--scope project` | `.mcp.json` в корне проекта | Для команды (коммитится в git) |
| `--scope user` | `~/.claude.json` | Кросс-проектный личный |

---

## Управление и отладка

```bash
claude mcp list                 # все серверы и статус
claude mcp get <имя>            # детали конкретного
claude mcp remove <имя>         # удалить
```

Внутри Claude Code: `/mcp` — статус всех серверов + авторизация OAuth.

### Если сервер не работает:
1. Проверь что пакет работает: `npx -y @namespace/package --help`
2. Увеличь таймаут: `MCP_TIMEOUT=10000 claude`
3. Увеличь лимит вывода: `MAX_MCP_OUTPUT_TOKENS=50000 claude`
4. Проверь логи: `~/.claude/logs/`

---

## Частые ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| Флаги игнорируются | Написаны после имени сервера | Перенести ДО имени |
| Connection closed | npx ждёт подтверждения | Добавить `-y` |
| Таймаут при старте | Медленная загрузка пакета | `MCP_TIMEOUT=10000` |
| Дубли конфигов | Есть и `~/.claude.json` и `~/.claude/mcp_servers.json` | Удалить дубль |

---

## Проверка после установки

```bash
claude mcp list | grep <имя>    # должен быть статус "✓ Connected"
```

Если "✗ Failed" — иди в раздел "Если сервер не работает" выше.

**Рапорт:** "Проверил: сервер `<имя>` в `claude mcp list` ✓, статус Connected ✓"
