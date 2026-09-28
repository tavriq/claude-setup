---
name: deploy
disable-model-invocation: true
description: Ручной деплой проекта на любой сервер через Docker + Git. Первый деплой (clone + build) и обновление (git pull + rebuild). Используй когда пользователь говорит "/deploy", "задеплой", "обнови на сервере", "залей на сервер". НЕ используй для изменения конфигов сервера без деплоя, работы с n8n (там свой workflow).
trigger: manual
allowed-tools: Bash, Read, AskUserQuestion
---

# Deploy: деплой на сервер

Используй когда: пользователь говорит "/deploy", "задеплой", "обнови на сервере", "залей на сервер" в Docker-проекте.

**НЕ используй для:**
- Изменения конфигов сервера без деплоя
- Работы с n8n (там свой workflow)
- Проектов с не-Docker деплоем (PM2, systemd) — для них заводи отдельный проектный агент/скилл со спецификой окружения
- Любого проекта без `Dockerfile` в корне (см. Шаг 0, проверка 1 — скилл сам остановится)

---

## .env переменные

Серверные переменные берутся из `ssh-connect`:

```env
SERVER_HOST=192.168.1.100
SERVER_PORT=22
SERVER_USER=root
SERVER_SSH_KEY=~/.ssh/id_ed25519
SERVER_PASSWORD=
SERVER_SUDO_PASSWORD=
```

Дополнительные переменные для деплоя:

```env
DEPLOY_PATH=/opt/myproject       # путь проекта на сервере
DEPLOY_BRANCH=main               # ветка для деплоя (по умолчанию: main или master)
```

Если `DEPLOY_PATH` нет — спроси у {{USER_NAME}} и предложи `/opt/<имя-проекта>/`.
Если `DEPLOY_BRANCH` нет — используй текущую ветку из `git branch --show-current`.

---

## Шаг 0: Предварительные проверки (локально)

Выполни ВСЕ проверки перед деплоем:

| # | Проверка | Команда | Если не пройдена |
|---|----------|---------|------------------|
| 1 | Dockerfile существует | `test -f Dockerfile` | "Нет Dockerfile. Запустить `/docker`?" |
| 2 | docker-compose.yml существует | `test -f docker-compose.yml` | "Нет docker-compose.yml. Запустить `/docker`?" |
| 3 | Git remote настроен | `git remote get-url origin` | "Нет remote. Создай репо на GitHub и добавь remote" |
| 4 | Все изменения закоммичены | `git status --porcelain` | Предложить закоммитить |
| 5 | Коммиты запушены | `git log @{u}..HEAD 2>/dev/null` | Предложить запушить |
| 6 | .env содержит SERVER_HOST | `grep SERVER_HOST .env` | Спросить данные сервера |
| 7 | SSH до сервера работает | ssh ... `echo ok` (через ssh-connect) | "Сервер недоступен" |

Если проверки 1-5 не проходят — предложи исправить и подожди. НЕ деплой с грязным состоянием.

---

## Шаг 1: Определить тип деплоя

```bash
# Через ssh-connect (expect):
ssh ... "test -d <DEPLOY_PATH>/.git && echo UPDATE || echo FIRST"
```

- `FIRST` — перейди к **Шаг 2A: Первый деплой**
- `UPDATE` — перейди к **Шаг 2B: Обновление**

Скажи {{USER_NAME}}: "Обнаружен [ПЕРВЫЙ ДЕПЛОЙ / ОБНОВЛЕНИЕ] для `<project-name>` на `<SERVER_HOST>`"

---

## Шаг 2A: Первый деплой

### 2A.1. Создать директорию на сервере

```bash
ssh ... "mkdir -p <DEPLOY_PATH>"
```

Если Permission denied — используй sudo:
```bash
ssh ... "sudo mkdir -p <DEPLOY_PATH> && sudo chown <SERVER_USER>:<SERVER_USER> <DEPLOY_PATH>"
```

### 2A.2. Проверить доступ к GitHub

```bash
ssh ... "git ls-remote <REPO_URL> HEAD 2>/dev/null && echo OK || echo FAIL"
```

Если FAIL (приватный репо) — выдай инструкцию:

```
Приватный репо. Настрой доступ на сервере:

Вариант 1 — PAT (Personal Access Token):
1. GitHub → Settings → Developer Settings → Fine-grained tokens → Generate
2. На сервере:
   git config --global credential.helper store
   git clone https://<USERNAME>:<PAT>@github.com/<OWNER>/<REPO>.git

Вариант 2 — Deploy Key:
1. На сервере: ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N ""
2. Скопируй публичный ключ: cat ~/.ssh/deploy_key.pub
3. GitHub → Repo → Settings → Deploy Keys → Add
4. git clone git@github.com:<OWNER>/<REPO>.git
```

Подожди пока {{USER_NAME}} настроит. Потом повтори проверку.

### 2A.3. Клонировать репо

```bash
ssh ... "git clone <REPO_URL> <DEPLOY_PATH> && cd <DEPLOY_PATH> && git checkout <DEPLOY_BRANCH>"
```

Если `DEPLOY_PATH` уже существует и не пуст (но без `.git`):
```bash
ssh ... "cd <DEPLOY_PATH> && git init && git remote add origin <REPO_URL> && git fetch && git checkout -f <DEPLOY_BRANCH>"
```

### 2A.4. Скопировать .env на сервер

```bash
# Через ssh-connect (expect SCP шаблон):
scp ... .env <SERVER_USER>@<SERVER_HOST>:<DEPLOY_PATH>/.env
```

Если `.env` локально нет — предупреди: "Нет локального .env. Если он нужен на сервере — создай и повтори `/deploy`."

### 2A.5. Собрать и запустить

```bash
ssh ... "cd <DEPLOY_PATH> && docker compose up -d --build"
```

Если `docker compose` не найден — попробуй `docker-compose`:
```bash
ssh ... "cd <DEPLOY_PATH> && docker-compose up -d --build"
```

### 2A.6. Перейди к **Шаг 3: Верификация**

---

## Шаг 2B: Обновление

### 2B.1. Git pull

```bash
ssh ... "cd <DEPLOY_PATH> && git pull origin <DEPLOY_BRANCH>"
```

Если git pull не проходит:

| Ошибка | Решение |
|--------|---------|
| `uncommitted changes` | Спроси {{USER_NAME}}: "На сервере есть локальные изменения. Сбросить? (git reset --hard origin/BRANCH)" |
| `merge conflict` | Спроси: "Конфликт при merge. Сбросить до remote? (git reset --hard origin/BRANCH)" |
| `authentication failed` | Токен истёк. Инструкция по обновлению PAT |

### 2B.2. Проверить .env

```bash
# Скачать .env с сервера во временный файл
scp ... <SERVER_USER>@<SERVER_HOST>:<DEPLOY_PATH>/.env /tmp/server_env_check

# Сравнить
diff .env /tmp/server_env_check
rm /tmp/server_env_check
```

Если есть различия — спроси: "Локальный .env отличается от серверного. Обновить на сервере?"

Если {{USER_NAME}} подтвердил:
```bash
scp ... .env <SERVER_USER>@<SERVER_HOST>:<DEPLOY_PATH>/.env
```

### 2B.3. Пересобрать и перезапустить

```bash
ssh ... "cd <DEPLOY_PATH> && docker compose up -d --build"
```

### 2B.4. Перейди к **Шаг 3: Верификация**

---

## Шаг 3: Верификация

### 3.1. Контейнеры запущены?

```bash
ssh ... "cd <DEPLOY_PATH> && docker compose ps"
```

Все контейнеры должны быть в статусе "Up". Если нет — покажи логи упавшего контейнера.

### 3.2. Логи без ошибок?

```bash
ssh ... "cd <DEPLOY_PATH> && docker compose logs --tail=30 2>&1"
```

Если в логах `Error`, `Exception`, `FATAL`, `Traceback` — покажи {{USER_NAME}}.

### 3.3. Вывести итог

```
Деплой завершён!
- Проект: <name>
- Сервер: <SERVER_HOST>
- Путь: <DEPLOY_PATH>
- Контейнеры: <список и статус>
- Логи: чисто / <N предупреждений>
```

---

## Откат (Rollback)

Если {{USER_NAME}} говорит "откати", "rollback", "верни предыдущую версию":

```bash
# Показать последние коммиты
ssh ... "cd <DEPLOY_PATH> && git log --oneline -5"

# Спросить на какой коммит откатить

# Откатить
ssh ... "cd <DEPLOY_PATH> && git checkout <COMMIT_HASH> && docker compose up -d --build"
```

Для возврата обратно на ветку:
```bash
ssh ... "cd <DEPLOY_PATH> && git checkout <DEPLOY_BRANCH> && docker compose up -d --build"
```

---

## Управление .env на сервере

| Действие | Как |
|----------|-----|
| Посмотреть | `ssh ... "cat <DEPLOY_PATH>/.env"` |
| Обновить из локального | `scp ... .env <USER>@<HOST>:<DEPLOY_PATH>/.env` |
| Перезапустить после обновления | `ssh ... "cd <DEPLOY_PATH> && docker compose restart"` |

---

## Обработка ошибок

| Ошибка | Решение |
|--------|---------|
| `git clone` не проходит | Приватный репо — настроить PAT/Deploy Key (см. шаг 2A.2) |
| `docker compose build` падает | Показать логи сборки. Попробовать `--no-cache` |
| Контейнер не стартует (exit 1) | `docker compose logs <service>` — показать {{USER_NAME}} |
| Порт занят | `ssh ... "sudo lsof -i :<PORT>"` — предложить другой порт |
| Нет места на диске | `ssh ... "df -h"` — предложить `docker system prune` |
| Docker не установлен | Инструкция: `curl -fsSL https://get.docker.com \| sh` |
| git не установлен | `sudo apt install git` / `sudo yum install git` |

---

## Важно

- Все SSH-команды выполняй через **ssh-connect** (expect-шаблоны). Никакого голого ssh/scp
- Все проекты деплоятся через **Docker** (docker compose). Без исключений
- Код доставляется через **git** (clone/pull), НЕ через scp
- **НЕ трогай** контейнеры и файлы других проектов на сервере
- **НЕ удаляй** ничего на сервере без подтверждения {{USER_NAME}}
