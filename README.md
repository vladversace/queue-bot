# Telegram бот для очереди на сдачу работ

## Установка

```bash
pip install -r requirements.txt
```

## Настройка

1. Создай бота через @BotFather в Telegram
2. Получи токен бота
3. Узнай свой Telegram ID — напиши боту `/myid`
4. Установи переменные окружения:

```bash
export BOT_TOKEN="твой_токен_бота"
export ADMIN_ID="твой_telegram_id"
export DASHBOARD_URL="https://твой-домен.com"
```

## Локальный запуск

### Бот
```bash
python bot.py
```

### Дашборд
```bash
python dashboard.py
```

Дашборд будет доступен на http://localhost:8080

## Команды бота

- `/start` — начало работы
- `/events` — список событий
- `/myid` — узнать свой Telegram ID
- `/add_event` — добавить событие (только админ)
- `/dashboard` — ссылка на дашборд

Админ также видит кнопку "Удалить событие" в меню события.

---

## Хостинг

### Вариант 1: Railway (рекомендую)

**Бесплатно $5/месяц, хватит надолго**

1. Зарегистрируйся на https://railway.app
2. Создай новый проект → Deploy from GitHub repo (залей код на GitHub)
3. Или: New Project → Empty Project → Add Service → Empty Service

**Для бота:**
- Settings → Start Command: `python bot.py`
- Variables: добавь `BOT_TOKEN`, `ADMIN_ID`, `DASHBOARD_URL`

**Для дашборда:**
- Создай второй сервис в том же проекте
- Start Command: `gunicorn dashboard:app --bind 0.0.0.0:$PORT`
- Settings → Networking → Generate Domain (получишь публичный URL)

### Вариант 2: Render

1. https://render.com → New Web Service
2. Подключи GitHub репо

**Дашборд (Web Service):**
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn dashboard:app --bind 0.0.0.0:$PORT`

**Бот (Background Worker):**
- Start Command: `python bot.py`

Environment Variables: `BOT_TOKEN`, `ADMIN_ID`, `DASHBOARD_URL`

### Вариант 3: VPS (DigitalOcean, Hetzner, etc.)

```bash
# Установи зависимости
pip install -r requirements.txt

# Запусти в фоне через screen
screen -S bot
python bot.py
# Ctrl+A, D — отключиться

screen -S dashboard
gunicorn dashboard:app --bind 0.0.0.0:8080
# Ctrl+A, D — отключиться
```

Для HTTPS используй nginx + certbot.

---

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Токен от @BotFather |
| `ADMIN_ID` | Твой Telegram ID (число) |
| `DASHBOARD_URL` | URL дашборда для команды /dashboard |

## Структура БД

SQLite файл `queue.db` создается автоматически.

Таблицы:
- `events` — события (id, name, max_positions)
- `queue` — записи (event_id, position, user_id, username, first_name)
