---
name: ssh-connect
disable-model-invocation: true
description: Надёжное SSH-подключение к любому серверу через expect. Используй когда нужно подключиться к серверу, выполнить SSH-команды, передать файлы (SCP/rsync), или когда SSH зависает/падает.
trigger: manual
allowed-tools: Bash, AskUserQuestion
---

# SSH-подключение через expect

**ВСЕГДА не используй голый `ssh`/`scp`/`rsync` — ВСЕГДА оборачивай в `expect`.** Без expect любой промпт заблокирует выполнение навсегда.

## .env переменные

```env
SERVER_HOST=192.168.1.100
SERVER_PORT=22                       # по умолчанию 22
SERVER_USER=root
SERVER_SSH_KEY=~/.ssh/id_ed25519    # если auth по ключу
SERVER_PASSWORD=                     # если auth по паролю
SERVER_SUDO_PASSWORD=                # если нужен sudo
```

Если переменных нет — спроси у {{USER_NAME}} через AskUserQuestion.

## Алгоритм

1. Прочитай `.env` → извлеки `SERVER_*` переменные
2. Нет `SERVER_HOST` → спроси параметры
3. Есть `SERVER_SSH_KEY` → key-based auth. Есть `SERVER_PASSWORD` → password-based. Оба → key-based
4. Собери expect-скрипт по шаблону, подставь параметры
5. При ошибке → см. таблицу проблем

## SSH-опции (добавляй ВСЕГДА)

```
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3"
```

## Шаблон: одна команда

```bash
# Key-based: добавь -i <KEY>
# Password-based: добавь -o PubkeyAuthentication=no и обработку "assword:" в expect
expect -c '
set timeout 30
spawn ssh -i <KEY> -p <PORT> -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 <USER>@<HOST> "<COMMAND>"
expect {
    timeout { puts "ОШИБКА: Таймаут"; exit 1 }
    "yes/no" { send "yes\r"; exp_continue }
    "assword:" { send "<PASSWORD>\r"; exp_continue }
    "Connection refused" { puts "ОШИБКА: Соединение отклонено"; exit 1 }
    "Permission denied" { puts "ОШИБКА: Доступ запрещён"; exit 1 }
    "No route to host" { puts "ОШИБКА: Сервер недоступен"; exit 1 }
    eof
}
foreach {pid spawnid os_error_flag value} [wait] break
exit $value
'
```

## Шаблон: команда с sudo

Флаг `-t` обязателен для sudo.

```bash
expect -c '
set timeout 30
spawn ssh -t -i <KEY> -p <PORT> -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 <USER>@<HOST> "sudo <COMMAND>"
expect {
    timeout { puts "ОШИБКА: Таймаут"; exit 1 }
    "assword" { send "<SUDO_PASSWORD>\r"; exp_continue }
    "Permission denied" { puts "ОШИБКА: Неверный sudo-пароль"; exit 1 }
    eof
}
foreach {pid spawnid os_error_flag value} [wait] break
exit $value
'
```

## Шаблон: несколько команд

```bash
expect -c '
set timeout 30
spawn ssh -i <KEY> -p <PORT> -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 <USER>@<HOST>
expect {
    timeout { puts "ОШИБКА: Таймаут"; exit 1 }
    "yes/no" { send "yes\r"; exp_continue }
    "assword:" { send "<PASSWORD>\r"; exp_continue }
    -re {\$ $|# $}
}
send "<COMMAND_1>\r"
expect -re {\$ $|# $}
send "<COMMAND_2>\r"
expect -re {\$ $|# $}
send "exit\r"
expect eof
'
```

## SCP через expect

```bash
# Upload: <LOCAL> <USER>@<HOST>:<REMOTE>
# Download: <USER>@<HOST>:<REMOTE> <LOCAL>
# Password: добавь "assword:" обработку
expect -c '
set timeout 120
spawn scp -i <KEY> -P <PORT> -o StrictHostKeyChecking=accept-new <LOCAL> <USER>@<HOST>:<REMOTE>
expect {
    timeout { puts "ОШИБКА: Таймаут передачи"; exit 1 }
    "yes/no" { send "yes\r"; exp_continue }
    "assword:" { send "<PASSWORD>\r"; exp_continue }
    eof
}
'
```

## rsync через expect

```bash
expect -c '
set timeout 300
spawn rsync -avz -e "ssh -i <KEY> -p <PORT> -o StrictHostKeyChecking=accept-new" <LOCAL> <USER>@<HOST>:<REMOTE>
expect {
    timeout { puts "ОШИБКА: Таймаут rsync"; exit 1 }
    "yes/no" { send "yes\r"; exp_continue }
    "assword:" { send "<PASSWORD>\r"; exp_continue }
    eof
}
'
```

## Проблемы и решения

| Симптом | Решение |
|---------|---------|
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | `ssh-keygen -R [<HOST>]:<PORT>` — **сначала спроси {{USER_NAME}}** (м.б. MITM) |
| `Connection timed out` | Увеличь ConnectTimeout=30, проверь: `nc -z -w5 <HOST> <PORT>` |
| `Permissions too open` | `chmod 600 <KEY_PATH>` |
| `a terminal is required` (sudo) | Добавь `-t` в ssh |
| `Connection refused` после попыток | Fail2Ban бан. **НЕ повторяй auth > 2 раз!** Подожди или спроси {{USER_NAME}} |

## Безопасность

- **НЕ удалять файлы** на сервере без подтверждения {{USER_NAME}}
- **НЕ трогать контейнеры/процессы** других проектов без подтверждения
- **Бэкап перед изменением конфигов:** `cp file file.bak`
- **НЕ повторяй auth > 2 раз** — риск бана Fail2Ban
