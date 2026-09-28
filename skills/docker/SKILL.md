---
name: docker
disable-model-invocation: true
description: Контейнеризация проектов через Docker. Создание Dockerfile, docker-compose.yml, .dockerignore. Используй когда пользователь создаёт сервис (бот, API, веб-приложение), говорит про Docker/контейнеры, просит задеплоить проект на сервер, или просит запустить/настроить сервис. НЕ используй для одноразовых скриптов, парсеров, утилит, контент-проектов.
---

# Docker: контейнеризация проектов

Используй когда: пользователь создаёт **новый** сервис (бот, API, веб-приложение) с нуля и хочет Docker; говорит про Docker/контейнеры; просит контейнеризовать существующий проект (явно).

**НЕ используй для:**
- Одноразовых скриптов, парсеров, утилит, контент-проектов
- Существующих проектов, которые уже работают **без Docker** (RN-мобильные, PM2-сервисы, статика, и т.п.) — если только {{USER_NAME}} **явно** не просит миграцию на Docker
- Проекты, у которых уже есть работающий не-Docker деплой (PM2, systemd, serverless). Если {{USER_NAME}} всё же попросит — сначала уточни, зачем менять то, что работает

---

## Главное правило

Все проекты-сервисы (боты, API, веб-приложения) — **только через Docker**. Без вопросов, без "а может без Docker?". Если проект будет работать постоянно — он в контейнере.

## Что создавать

Для каждого сервисного проекта обязательно:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

## Автодетект стека

Определи стек по файлам в проекте:

| Файл | Стек | Базовый образ |
|------|------|---------------|
| `requirements.txt` / `pyproject.toml` | Python | `python:3.x-slim` |
| `package.json` | Node.js | `node:lts-alpine` |
| `go.mod` | Go | `golang:1.x-alpine` (build) + `alpine` (runtime) |
| `Cargo.toml` | Rust | `rust:1.x` (build) + `debian:slim` (runtime) |

Если стек не определяется — спроси у {{USER_NAME}}.

## Умный rebuild

**НЕ пересобирай контейнер после каждого изменения кода!**

| Что изменилось | Действие |
|----------------|----------|
| Код (`.py`, `.js`, `.go` и т.д.) | НЕ rebuild. Используй **volumes** — код маппится в контейнер, изменения видны мгновенно |
| Зависимости (`requirements.txt`, `package.json`, `go.mod`) | Rebuild: `docker-compose up --build` |
| `Dockerfile` или `docker-compose.yml` | Rebuild: `docker-compose up --build` |
| Деплой на сервер | Всегда полный rebuild |

## Безопасность

- `.env` передавать через `env_file` в docker-compose
- **НИКОГДА** не копировать `.env` в образ (не `COPY .env`)
- **НИКОГДА** не хардкодить секреты в Dockerfile
- Добавить `.env` в `.dockerignore`

## Best practices для Dockerfile

1. **Slim/alpine образы** — минимальный размер
2. **Non-root user** — не запускать приложение от root
3. **Multi-stage build** для прода (особенно Go, Rust, фронтенд)
4. **Кэширование слоёв** — сначала копировать зависимости, потом код:
   ```dockerfile
   COPY requirements.txt .
   RUN pip install -r requirements.txt
   COPY . .
   ```
5. **HEALTHCHECK** — для сервисов с HTTP

## Шаблон docker-compose.yml

```yaml
services:
  app:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./src:/app/src  # live-reload для разработки
    ports:
      - "${PORT:-8000}:8000"
```

## Шаблон .dockerignore

```
.env
.git
__pycache__
*.pyc
node_modules
.venv
venv
tmp/
*.log
.DS_Store
```

## Порядок действий

1. Определить стек проекта (автодетект или спросить)
2. Создать `Dockerfile` по best practices
3. Создать `docker-compose.yml` с volumes для dev
4. Создать `.dockerignore`
5. Проверить сборку: `docker-compose build`
6. Запустить: `docker-compose up -d`
7. Проверить логи: `docker-compose logs -f`
8. **Рапорт:** "Проверил: сборка ✓, контейнер запущен ✓, логи чистые ✓"
