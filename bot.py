import asyncio
import logging
import os
import hashlib
import aiohttp
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import database as db

# Log file path
LOG_PATH = os.getenv("LOG_PATH", "/data/bot.log")
BSUIR_GROUP = os.getenv("BSUIR_GROUP", "521701")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add file handler
try:
    file_handler = logging.FileHandler(LOG_PATH, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
except Exception:
    pass  # If can't create log file, continue with console only


def log_action(user_id: int, username: str, action: str):
    """Log user actions"""
    logger.info(f"[USER:{user_id}|@{username}] {action}")


def generate_fake_id(username: str) -> int:
    """Generate consistent fake ID for unknown users"""
    hash_obj = hashlib.sha256(username.lower().encode())
    return int(hash_obj.hexdigest()[:15], 16)

# Bot token from environment variable
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
ALLOWED_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip().isdigit()]
SUBGROUP1_IDS = [int(x.strip()) for x in os.getenv("SUBGROUP1_IDS", "").split(",") if x.strip().isdigit()]
SUBGROUP2_IDS = [int(x.strip()) for x in os.getenv("SUBGROUP2_IDS", "").split(",") if x.strip().isdigit()]
FORUM_CHAT_ID = int(os.getenv("FORUM_CHAT_ID", "0"))
FORUM_THREAD_ID = int(os.getenv("FORUM_THREAD_ID", "0"))
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8080")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_IDS:  # если список пустой — доступ всем
        return True
    return user_id in ALLOWED_IDS or user_id in ADMIN_IDS


def get_user_subgroup(user_id: int) -> int:
    """0 = не в подгруппе, 1 = первая, 2 = вторая"""
    if user_id in SUBGROUP1_IDS:
        return 1
    if user_id in SUBGROUP2_IDS:
        return 2
    return 0


def can_register_for_event(user_id: int, event_subgroup: int) -> bool:
    """Проверяет может ли пользователь записаться на событие"""
    if event_subgroup == 0:  # общее событие
        return True
    if is_admin(user_id):  # админы могут везде
        return True
    return get_user_subgroup(user_id) == event_subgroup


# Pending exchange requests: {target_user_id: {from_user_id, from_username, event_id, event_name}}
pending_exchanges = {}


class QueueStates(StatesGroup):
    waiting_for_position = State()
    waiting_for_event_name = State()
    waiting_for_max_positions = State()
    waiting_for_subgroup = State()
    waiting_for_new_name = State()


def get_events_keyboard() -> InlineKeyboardMarkup:
    events = db.get_events()
    buttons = []
    for event in events:
        buttons.append([InlineKeyboardButton(
            text=event["name"],
            callback_data=f"event_{event['id']}"
        )])
    if not buttons:
        buttons.append([InlineKeyboardButton(text="Нет событий", callback_data="no_events")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_event_actions_keyboard(event_id: int, user_id: int = 0) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📝 Записаться", callback_data=f"register_{event_id}")],
        [InlineKeyboardButton(text="📋 Посмотреть очередь", callback_data=f"queue_{event_id}")],
        [InlineKeyboardButton(text="❌ Отменить запись", callback_data=f"cancel_{event_id}")],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"rename_{event_id}")])
        buttons.append([InlineKeyboardButton(text="🗑 Удалить событие", callback_data=f"delete_{event_id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_events")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        await message.answer("У тебя нет доступа к этому боту.")
        return
    
    # Проверяем deep link (например /start register_5)
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("register_"):
        try:
            event_id = int(args[1].replace("register_", ""))
            event = db.get_event_by_id(event_id)
            
            if not event:
                await message.answer("Событие не найдено", reply_markup=get_events_keyboard())
                return
            
            # Проверка подгруппы
            event_subgroup = event.get("subgroup", 0)
            if not can_register_for_event(message.from_user.id, event_subgroup):
                subgroup_names = {1: "1 подгруппы", 2: "2 подгруппы"}
                await message.answer(f"Только для {subgroup_names[event_subgroup]}", reply_markup=get_events_keyboard())
                return
            
            await state.update_data(event_id=event_id)
            await state.set_state(QueueStates.waiting_for_position)
            
            queue = db.get_queue(event_id)
            taken_positions = [q["position"] for q in queue]
            available = [i for i in range(1, event["max_positions"] + 1) if i not in taken_positions]
            
            if not available:
                await message.answer("Все позиции заняты", reply_markup=get_events_keyboard())
                await state.clear()
                return
            
            nearest = available[0]
            available_str = ", ".join(map(str, available[:15]))
            if len(available) > 15:
                available_str += f"... (ещё {len(available) - 15})"
            
            await message.answer(
                f"📌 {event['name']}\n\n"
                f"Введи номер позиции (1-{event['max_positions']})\n\n"
                f"Ближайшая свободная: {nearest}\n"
                f"Свободные: {available_str}"
            )
            return
        except (ValueError, IndexError):
            pass
    
    help_text = (
        "📋 Бот для записи в очередь на сдачу работ\n\n"
        "👤 Команды:\n"
        "/events — список событий\n"
        "/dashboard — ссылка на дашборд\n\n"
        "💬 Команды в чате группы:\n"
        "/q <событие> [позиция] — записаться\n"
        "/c <событие> — отменить запись\n"
        "/e @user <событие> — обмен местами"
    )
    
    if is_admin(message.from_user.id):
        help_text += (
            "\n\n🔧 Админ-команды:\n"
            "/add_event — создать событие\n"
            "/schedule — загрузить лабы из iis.bsuir.by\n"
            "/set @user <событие> <позиция> — записать\n"
            "/kick @user <событие> — исключить\n"
            "/clear <событие> — очистить очередь\n"
            "/backup — скачать базу данных\n"
            "/logs — скачать логи"
        )
    
    await message.answer(help_text, reply_markup=get_events_keyboard())


@dp.message(Command("events"))
async def cmd_events(message: types.Message):
    if not is_allowed(message.from_user.id):
        await message.answer("У тебя нет доступа к этому боту.")
        return
    await message.answer("Выбери событие:", reply_markup=get_events_keyboard())


@dp.message(Command("dashboard"))
async def cmd_dashboard(message: types.Message):
    dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:8080")
    await message.answer(f"Дашборд: {dashboard_url}")


@dp.message(Command("q"))
async def cmd_quick_register(message: types.Message):
    if not is_allowed(message.from_user.id):
        return
    
    # Парсим аргументы: /q <keyword> [position]
    args = message.text.split()[1:]  # убираем /q
    
    if not args:
        reply = await message.reply("Использование: /q <название> [позиция]")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    # Проверяем последний аргумент - число или нет
    position = None
    if len(args) >= 2 and args[-1].isdigit():
        position = int(args[-1])
        keyword = " ".join(args[:-1])
    else:
        keyword = " ".join(args)
    
    log_action(message.from_user.id, message.from_user.username, f"Q_ATTEMPT keyword='{keyword}'")
    
    # Ищем событие
    event = db.find_event_by_keyword(keyword)
    
    if not event:
        log_action(message.from_user.id, message.from_user.username, f"Q_NOT_FOUND keyword='{keyword}'")
        reply = await message.reply(f"Событие '{keyword}' не найдено")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    # Проверка подгруппы
    event_subgroup = event.get("subgroup", 0)
    if not can_register_for_event(message.from_user.id, event_subgroup):
        subgroup_names = {1: "1 подгруппы", 2: "2 подгруппы"}
        reply = await message.reply(f"Только для {subgroup_names[event_subgroup]}")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    # Если позиция не указана - берём ближайшую свободную
    queue = db.get_queue(event["id"])
    taken_positions = [q["position"] for q in queue]
    available = [i for i in range(1, event["max_positions"] + 1) if i not in taken_positions]
    
    if not available:
        reply = await message.reply("Все позиции заняты")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    if position is None:
        position = available[0]
    
    # Регистрируем
    user = message.from_user
    success, msg = db.register_position(
        event_id=event["id"],
        position=position,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    
    if success:
        log_action(user.id, user.username, f"REGISTER {event['name']} pos {position}")
        reply = await message.reply(f"✅ Вы записаны в очередь «{event['name']}» на позицию {position}")
    else:
        reply = await message.reply(f"❌ {msg}")
    
    # Удаляем сообщения
    await asyncio.sleep(3)
    try:
        await message.delete()
    except:
        pass
    await asyncio.sleep(2)
    try:
        await reply.delete()
    except:
        pass


@dp.message(Command("c"))
async def cmd_cancel_forum(message: types.Message):
    """Cancel registration from forum: /c <event>"""
    if not is_allowed(message.from_user.id):
        return
    
    args = message.text.split()[1:]
    
    if not args:
        reply = await message.reply("Использование: /c <название-события>")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    keyword = " ".join(args)
    event = db.find_event_by_keyword(keyword)
    
    if not event:
        reply = await message.reply(f"Событие '{keyword}' не найдено")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    success, msg = db.cancel_registration(event["id"], message.from_user.id)
    
    if success:
        log_action(message.from_user.id, message.from_user.username, f"CANCEL {event['name']}")
        reply = await message.reply(f"✅ Вы освободили место в очереди «{event['name']}»")
    else:
        reply = await message.reply(f"❌ Вы не записаны в «{event['name']}»")
    
    await asyncio.sleep(3)
    try:
        await message.delete()
    except:
        pass
    await asyncio.sleep(2)
    try:
        await reply.delete()
    except:
        pass


@dp.message(Command("e"))
async def cmd_exchange(message: types.Message):
    """Exchange request: /e @username <event>"""
    if not is_allowed(message.from_user.id):
        return
    
    args = message.text.split()[1:]
    
    if len(args) < 2 or not args[0].startswith("@"):
        reply = await message.reply("Использование: /e @username <событие>")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    target_username = args[0][1:]  # убираем @
    keyword = " ".join(args[1:])
    
    event = db.find_event_by_keyword(keyword)
    
    if not event:
        reply = await message.reply(f"Событие '{keyword}' не найдено")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    # Проверяем что инициатор записан
    my_position = db.get_user_position(event["id"], message.from_user.id)
    if not my_position:
        reply = await message.reply(f"Вы не записаны в «{event['name']}»")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    # Ищем target в очереди
    queue = db.get_queue(event["id"])
    target_user = None
    for q in queue:
        if q["username"] and q["username"].lower() == target_username.lower():
            target_user = q
            break
    
    if not target_user:
        reply = await message.reply(f"@{target_username} не найден в очереди «{event['name']}»")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    if target_user["user_id"] == message.from_user.id:
        reply = await message.reply("Нельзя меняться с самим собой")
        await asyncio.sleep(5)
        try:
            await message.delete()
            await reply.delete()
        except:
            pass
        return
    
    # Сохраняем pending exchange
    pending_exchanges[target_user["user_id"]] = {
        "from_user_id": message.from_user.id,
        "from_username": message.from_user.username or message.from_user.first_name,
        "from_position": my_position,
        "target_position": target_user["position"],
        "event_id": event["id"],
        "event_name": event["name"]
    }
    
    # Отправляем запрос target пользователю
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"exchange_accept_{message.from_user.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"exchange_decline_{message.from_user.id}")
        ]
    ])
    
    try:
        await bot.send_message(
            target_user["user_id"],
            f"🔄 Запрос на обмен местами\n\n"
            f"Событие: {event['name']}\n"
            f"@{message.from_user.username or message.from_user.first_name} (позиция {my_position}) хочет поменяться с вами (позиция {target_user['position']})",
            reply_markup=keyboard
        )
        reply = await message.reply(f"✅ Запрос на обмен отправлен @{target_username}")
    except:
        reply = await message.reply(f"❌ Не удалось отправить запрос @{target_username}. Пользователь не начал диалог с ботом.")
    
    await asyncio.sleep(3)
    try:
        await message.delete()
    except:
        pass
    await asyncio.sleep(2)
    try:
        await reply.delete()
    except:
        pass


@dp.callback_query(F.data.startswith("exchange_accept_"))
async def callback_exchange_accept(callback: CallbackQuery):
    from_user_id = int(callback.data.split("_")[2])
    
    exchange = pending_exchanges.get(callback.from_user.id)
    if not exchange or exchange["from_user_id"] != from_user_id:
        await callback.answer("Запрос устарел")
        await callback.message.delete()
        return
    
    # Делаем обмен
    success = db.swap_positions(exchange["event_id"], from_user_id, callback.from_user.id)
    
    if success:
        log_action(callback.from_user.id, callback.from_user.username, f"EXCHANGE_ACCEPT {exchange['event_name']} with user {from_user_id}")
        await callback.message.edit_text(
            f"✅ Обмен выполнен!\n\n"
            f"Событие: {exchange['event_name']}\n"
            f"Ваша новая позиция: {exchange['from_position']}"
        )
        # Уведомляем инициатора
        try:
            await bot.send_message(
                from_user_id,
                f"✅ @{callback.from_user.username or callback.from_user.first_name} принял обмен!\n\n"
                f"Событие: {exchange['event_name']}\n"
                f"Ваша новая позиция: {exchange['target_position']}"
            )
        except:
            pass
    else:
        await callback.message.edit_text("❌ Ошибка при обмене")
    
    del pending_exchanges[callback.from_user.id]


@dp.callback_query(F.data.startswith("exchange_decline_"))
async def callback_exchange_decline(callback: CallbackQuery):
    from_user_id = int(callback.data.split("_")[2])
    
    exchange = pending_exchanges.get(callback.from_user.id)
    if not exchange or exchange["from_user_id"] != from_user_id:
        await callback.answer("Запрос устарел")
        await callback.message.delete()
        return
    
    await callback.message.edit_text("❌ Вы отклонили запрос на обмен")
    
    # Уведомляем инициатора
    try:
        await bot.send_message(
            from_user_id,
            f"❌ @{callback.from_user.username or callback.from_user.first_name} отклонил запрос на обмен в «{exchange['event_name']}»"
        )
    except:
        pass
    
    del pending_exchanges[callback.from_user.id]


@dp.message(Command("set"))
async def cmd_admin_set(message: types.Message):
    """Admin command: /set @username <event> <position>"""
    if not is_admin(message.from_user.id):
        await message.answer("Только для админов")
        return
    
    args = message.text.split()[1:]
    
    if len(args) < 3 or not args[0].startswith("@"):
        await message.answer("Использование: /set @username <событие> <позиция>")
        return
    
    username = args[0][1:]  # убираем @
    
    # Последний аргумент - позиция
    if not args[-1].isdigit():
        await message.answer("Позиция должна быть числом")
        return
    
    position = int(args[-1])
    keyword = " ".join(args[1:-1])
    
    event = db.find_event_by_keyword(keyword)
    
    if not event:
        await message.answer(f"Событие '{keyword}' не найдено")
        return
    
    # Получаем user_id и first_name по username из очереди или генерируем фейковый
    # Сначала проверяем есть ли в любой очереди
    all_data = db.get_all_data()
    user_id = None
    first_name = None
    for ev in all_data.values():
        for q in ev["queue"]:
            if q.get("username") and q["username"].lower() == username.lower():
                user_id = q["user_id"]
                first_name = q.get("first_name")
                break
        if user_id:
            break
    
    # Если не нашли - генерируем ID на основе username
    if not user_id:
        user_id = generate_fake_id(username)
    
    success, msg = db.admin_register(event["id"], position, user_id, username, first_name)
    log_action(message.from_user.id, message.from_user.username, f"ADMIN_SET @{username} to {event['name']} pos {position}: {success}")
    await message.answer(f"{msg}\nСобытие: {event['name']}")


@dp.message(Command("clear"))
async def cmd_admin_clear(message: types.Message):
    """Admin command: /clear <event>"""
    if not is_admin(message.from_user.id):
        await message.answer("Только для админов")
        return
    
    args = message.text.split()[1:]
    
    if not args:
        await message.answer("Использование: /clear <событие>")
        return
    
    keyword = " ".join(args)
    event = db.find_event_by_keyword(keyword)
    
    if not event:
        await message.answer(f"Событие '{keyword}' не найдено")
        return
    
    deleted = db.clear_queue(event["id"])
    log_action(message.from_user.id, message.from_user.username, f"ADMIN_CLEAR {event['name']} deleted {deleted}")
    await message.answer(f"✅ Очередь «{event['name']}» очищена\nУдалено записей: {deleted}")


@dp.message(Command("kick"))
async def cmd_admin_kick(message: types.Message):
    """Admin command: /kick @username <event>"""
    if not is_admin(message.from_user.id):
        await message.answer("Только для админов")
        return
    
    args = message.text.split()[1:]
    
    if len(args) < 2 or not args[0].startswith("@"):
        await message.answer("Использование: /kick @username <событие>")
        return
    
    username = args[0][1:]  # убираем @
    keyword = " ".join(args[1:])
    
    event = db.find_event_by_keyword(keyword)
    
    if not event:
        await message.answer(f"Событие '{keyword}' не найдено")
        return
    
    success, msg = db.kick_user(event["id"], username)
    log_action(message.from_user.id, message.from_user.username, f"ADMIN_KICK @{username} from {event['name']}: {success}")
    await message.answer(f"{msg}\nСобытие: {event['name']}")


@dp.message(Command("backup"))
async def cmd_backup(message: types.Message):
    """Admin command: /backup - download database file"""
    if not is_admin(message.from_user.id):
        await message.answer("Только для админов")
        return
    
    db_path = db.DB_PATH
    if not os.path.exists(db_path):
        await message.answer("База данных не найдена")
        return
    
    try:
        backup_file = FSInputFile(db_path, filename=f"queue_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        await message.answer_document(backup_file, caption="📦 Бэкап базы данных")
        log_action(message.from_user.id, message.from_user.username, "BACKUP downloaded")
    except Exception as e:
        await message.answer(f"Ошибка создания бэкапа: {e}")


@dp.message(Command("logs"))
async def cmd_logs(message: types.Message):
    """Admin command: /logs - download log file"""
    if not is_admin(message.from_user.id):
        await message.answer("Только для админов")
        return
    
    if not os.path.exists(LOG_PATH):
        await message.answer("Файл логов не найден")
        return
    
    if os.path.getsize(LOG_PATH) == 0:
        await message.answer("Логи пока пустые")
        return
    
    try:
        log_file = FSInputFile(LOG_PATH, filename=f"bot_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        await message.answer_document(log_file, caption="📋 Логи бота")
        log_action(message.from_user.id, message.from_user.username, "LOGS downloaded")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


@dp.message(Command("schedule"))
async def cmd_schedule(message: types.Message):
    """Admin command: /schedule - fetch labs from BSUIR and create events"""
    if not is_admin(message.from_user.id):
        await message.answer("Только для админов")
        return
    
    await message.answer(f"⏳ Загружаю расписание группы {BSUIR_GROUP}...")
    
    try:
        async with aiohttp.ClientSession() as session:
            # Получаем текущую неделю
            async with session.get(
                "https://iis.bsuir.by/api/v1/schedule/current-week",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                current_week = await resp.json() if resp.status == 200 else 1
            
            # Получаем расписание
            async with session.get(
                f"https://iis.bsuir.by/api/v1/schedule?studentGroup={BSUIR_GROUP}",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    await message.answer(f"Ошибка API: {resp.status}")
                    return
                data = await resp.json()
        
        # Парсим лабы
        labs = []
        days_map = {
            "Понедельник": 0, "Вторник": 1, "Среда": 2, 
            "Четверг": 3, "Пятница": 4, "Суббота": 5
        }
        
        # Минск UTC+3
        minsk_tz = timezone(timedelta(hours=3))
        today = datetime.now(minsk_tz).date()
        # Находим понедельник текущей недели
        monday = today - timedelta(days=today.weekday())
        
        for day_name, day_offset in days_map.items():
            day_schedule = data.get("schedules", {}).get(day_name, [])
            for lesson in day_schedule:
                lesson_type = lesson.get("lessonTypeAbbrev", "")
                if lesson_type == "ЛР":  # Лабораторная работа
                    subject = lesson.get("subject", "")
                    subgroup = lesson.get("numSubgroup", 0)
                    time_start = lesson.get("startLessonTime", "")
                    time_end = lesson.get("endLessonTime", "")
                    weeks = lesson.get("weekNumber", [])
                    
                    # Вычисляем конкретные даты
                    for week in weeks:
                        week_diff = week - current_week
                        lab_date = monday + timedelta(days=day_offset + week_diff * 7)
                        
                        # Только сегодня и будущие
                        if lab_date >= today:
                            labs.append({
                                "subject": subject,
                                "subgroup": subgroup,
                                "day": day_name,
                                "date": lab_date,
                                "time": f"{time_start}-{time_end}",
                                "week": week
                            })
        
        # Сортируем по дате
        labs.sort(key=lambda x: x["date"])
        
        if not labs:
            await message.answer("Лабораторных работ не найдено")
            return
        
        # Показываем найденные лабы
        text = f"📚 Найдено {len(labs)} лабораторных (с {today.strftime('%d.%m')}):\n\n"
        for i, lab in enumerate(labs[:15], 1):
            sub_text = f" (подгр. {lab['subgroup']})" if lab['subgroup'] else ""
            date_str = lab["date"].strftime("%d.%m")
            text += f"{i}. {lab['subject']}{sub_text}\n   📅 {date_str} ({lab['day']}) {lab['time']}\n"
        
        if len(labs) > 15:
            text += f"\n... и ещё {len(labs) - 15}"
        
        text += "\n\nСоздать события из расписания? /create_from_schedule"
        
        # Сохраняем в памяти для создания
        pending_schedule[message.from_user.id] = labs
        
        await message.answer(text)
        log_action(message.from_user.id, message.from_user.username, f"SCHEDULE fetched {len(labs)} labs")
        
    except asyncio.TimeoutError:
        await message.answer("Таймаут запроса к API")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


# Store fetched schedule temporarily
pending_schedule = {}


@dp.message(Command("create_from_schedule"))
async def cmd_create_from_schedule(message: types.Message):
    """Create events from fetched schedule"""
    if not is_admin(message.from_user.id):
        await message.answer("Только для админов")
        return
    
    labs = pending_schedule.get(message.from_user.id)
    if not labs:
        await message.answer("Сначала используй /schedule")
        return
    
    created = 0
    skipped = 0
    
    for lab in labs:
        # Название: "ПРЕДМЕТ дата"
        date_str = lab['date'].strftime("%d.%m")
        event_name = f"{lab['subject']} {date_str}"
        
        # Определяем подгруппу (0 = общее, 1 = первая, 2 = вторая)
        subgroup = lab['subgroup'] if lab['subgroup'] in [1, 2] else 0
        
        # Проверяем нет ли уже такого события
        existing = db.find_event_by_keyword(event_name)
        if existing:
            skipped += 1
            continue
        
        if db.add_event(event_name, 30, subgroup):
            created += 1
    
    del pending_schedule[message.from_user.id]
    
    await message.answer(f"✅ Создано событий: {created}\n⏭ Пропущено (уже есть): {skipped}")
    log_action(message.from_user.id, message.from_user.username, f"SCHEDULE created {created} events")


@dp.message(Command("add_event"))
async def cmd_add_event(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Только админ может добавлять события")
        return
    await message.answer("Введи название события:")
    await state.set_state(QueueStates.waiting_for_event_name)


@dp.message(QueueStates.waiting_for_event_name)
async def process_event_name(message: types.Message, state: FSMContext):
    await state.update_data(event_name=message.text)
    await message.answer("Сколько максимум позиций? (число, по умолчанию 30)")
    await state.set_state(QueueStates.waiting_for_max_positions)


@dp.message(QueueStates.waiting_for_max_positions)
async def process_max_positions(message: types.Message, state: FSMContext):
    try:
        max_pos = int(message.text) if message.text.strip() else 30
    except ValueError:
        max_pos = 30
    
    await state.update_data(max_positions=max_pos)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Общее (все)", callback_data="subgroup_0")],
        [InlineKeyboardButton(text="1 подгруппа", callback_data="subgroup_1")],
        [InlineKeyboardButton(text="2 подгруппа", callback_data="subgroup_2")],
    ])
    await message.answer("Выбери подгруппу:", reply_markup=keyboard)
    await state.set_state(QueueStates.waiting_for_subgroup)


@dp.callback_query(F.data.startswith("subgroup_"))
async def process_subgroup(callback: CallbackQuery, state: FSMContext):
    subgroup = int(callback.data.split("_")[1])
    data = await state.get_data()
    event_name = data["event_name"]
    max_pos = data["max_positions"]
    
    subgroup_names = {0: "все", 1: "1 подгруппа", 2: "2 подгруппа"}
    
    if db.add_event(event_name, max_pos, subgroup):
        log_action(callback.from_user.id, callback.from_user.username, f"CREATE_EVENT {event_name} max={max_pos} subgroup={subgroup}")
        await callback.message.edit_text(
            f"Событие '{event_name}' создано\n"
            f"Мест: {max_pos}\n"
            f"Подгруппа: {subgroup_names[subgroup]}"
        )
        
        # Уведомление в форум
        if FORUM_CHAT_ID and FORUM_THREAD_ID:
            subgroup_text = ""
            if subgroup == 1:
                subgroup_text = "\n👥 Только 1 подгруппа"
            elif subgroup == 2:
                subgroup_text = "\n👥 Только 2 подгруппа"
            
            try:
                msg = await bot.send_message(
                    chat_id=FORUM_CHAT_ID,
                    message_thread_id=FORUM_THREAD_ID,
                    text=f"📢 Новое событие: {event_name}\n"
                         f"Мест: {max_pos}{subgroup_text}\n\n"
                         f"Для записи: /q {event_name.split()[0]} [позиция]\n\n"
                         f"📊 Дашборд: {DASHBOARD_URL}"
                )
                logger.info(f"Forum notification sent for {event_name}")
                # Закрепляем сообщение
                try:
                    await bot.pin_chat_message(
                        chat_id=FORUM_CHAT_ID,
                        message_id=msg.message_id,
                        disable_notification=True
                    )
                except Exception as e:
                    logger.warning(f"Failed to pin message: {e}")
            except Exception as e:
                logger.error(f"Failed to send forum notification: {e}")
        else:
            logger.warning(f"Forum notification skipped: CHAT_ID={FORUM_CHAT_ID}, THREAD_ID={FORUM_THREAD_ID}")
    else:
        await callback.message.edit_text(f"Событие '{event_name}' уже существует")
    
    await state.clear()


@dp.callback_query(F.data == "no_events")
async def callback_no_events(callback: CallbackQuery):
    await callback.answer("Событий пока нет. Используй /add_event")


@dp.callback_query(F.data == "back_to_events")
async def callback_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Выбери событие:", reply_markup=get_events_keyboard())


@dp.callback_query(F.data.startswith("event_"))
async def callback_event_selected(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("У тебя нет доступа")
        return
    event_id = int(callback.data.split("_")[1])
    event = db.get_event_by_id(event_id)
    if not event:
        await callback.answer("Событие не найдено")
        return
    
    queue = db.get_queue(event_id)
    taken = len(queue)
    
    subgroup = event.get("subgroup", 0)
    subgroup_text = ""
    if subgroup == 1:
        subgroup_text = "\n👥 Только 1 подгруппа"
    elif subgroup == 2:
        subgroup_text = "\n👥 Только 2 подгруппа"
    
    await callback.message.edit_text(
        f"📌 {event['name']}\n"
        f"Занято: {taken}/{event['max_positions']}{subgroup_text}",
        reply_markup=get_event_actions_keyboard(event_id, callback.from_user.id)
    )


@dp.callback_query(F.data.startswith("register_"))
async def callback_register(callback: CallbackQuery, state: FSMContext):
    if not is_allowed(callback.from_user.id):
        await callback.answer("У тебя нет доступа")
        return
    event_id = int(callback.data.split("_")[1])
    event = db.get_event_by_id(event_id)
    
    # Проверка подгруппы
    event_subgroup = event.get("subgroup", 0)
    if not can_register_for_event(callback.from_user.id, event_subgroup):
        subgroup_names = {1: "1 подгруппы", 2: "2 подгруппы"}
        await callback.answer(f"Только для {subgroup_names[event_subgroup]}")
        return
    
    await state.update_data(event_id=event_id)
    await state.set_state(QueueStates.waiting_for_position)
    
    queue = db.get_queue(event_id)
    taken_positions = [q["position"] for q in queue]
    
    available = [i for i in range(1, event["max_positions"] + 1) if i not in taken_positions]
    
    if not available:
        await callback.message.edit_text(
            "Все позиции заняты",
            reply_markup=get_event_actions_keyboard(event_id, callback.from_user.id)
        )
        await state.clear()
        return
    
    nearest = available[0]
    available_str = ", ".join(map(str, available[:15]))
    if len(available) > 15:
        available_str += f"... (ещё {len(available) - 15})"
    
    await callback.message.edit_text(
        f"Введи номер позиции (1-{event['max_positions']})\n\n"
        f"Ближайшая свободная: {nearest}\n"
        f"Свободные: {available_str}"
    )


@dp.message(QueueStates.waiting_for_position)
async def process_position(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    event_id = data.get("event_id")
    
    if not event_id:
        await state.clear()
        return
    
    try:
        position = int(message.text)
    except ValueError:
        await message.answer("Введи число")
        return
    
    user = message.from_user
    success, msg = db.register_position(
        event_id=event_id,
        position=position,
        user_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    
    await message.answer(msg, reply_markup=get_events_keyboard())
    await state.clear()


@dp.callback_query(F.data.startswith("queue_"))
async def callback_queue(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("У тебя нет доступа")
        return
    event_id = int(callback.data.split("_")[1])
    event = db.get_event_by_id(event_id)
    queue = db.get_queue(event_id)
    
    if not queue:
        text = f"📋 {event['name']}\n\nОчередь пуста"
    else:
        lines = [f"📋 {event['name']}\n"]
        for q in queue:
            name = q["first_name"] or q["username"] or "—"
            lines.append(f"{q['position']}. {name}")
        text = "\n".join(lines)
    
    await callback.message.edit_text(
        text,
        reply_markup=get_event_actions_keyboard(event_id, callback.from_user.id)
    )


@dp.callback_query(F.data.startswith("cancel_"))
async def callback_cancel(callback: CallbackQuery):
    if not is_allowed(callback.from_user.id):
        await callback.answer("У тебя нет доступа")
        return
    event_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    success, msg = db.cancel_registration(event_id, user_id)
    await callback.answer(msg)
    
    if success:
        event = db.get_event_by_id(event_id)
        log_action(callback.from_user.id, callback.from_user.username, f"CANCEL {event['name']}")
        queue = db.get_queue(event_id)
        await callback.message.edit_text(
            f"📌 {event['name']}\n"
            f"Занято: {len(queue)}/{event['max_positions']}",
            reply_markup=get_event_actions_keyboard(event_id, callback.from_user.id)
        )


@dp.callback_query(F.data.startswith("rename_"))
async def callback_rename(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Только админ может изменять события")
        return
    
    event_id = int(callback.data.split("_")[1])
    event = db.get_event_by_id(event_id)
    
    await state.update_data(rename_event_id=event_id)
    await state.set_state(QueueStates.waiting_for_new_name)
    await callback.message.edit_text(f"Текущее название: {event['name']}\n\nВведи новое название:")


@dp.message(QueueStates.waiting_for_new_name)
async def process_new_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    event_id = data.get("rename_event_id")
    
    if not event_id:
        await state.clear()
        return
    
    new_name = message.text.strip()
    success = db.rename_event(event_id, new_name)
    
    if success:
        await message.answer(f"Название изменено на: {new_name}", reply_markup=get_events_keyboard())
    else:
        await message.answer("Ошибка: событие с таким названием уже существует", reply_markup=get_events_keyboard())
    
    await state.clear()


@dp.callback_query(F.data.startswith("delete_"))
async def callback_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Только админ может удалять события")
        return
    
    event_id = int(callback.data.split("_")[1])
    event = db.get_event_by_id(event_id)
    
    if event:
        db.delete_event(event_id)
        log_action(callback.from_user.id, callback.from_user.username, f"DELETE_EVENT {event['name']}")
        await callback.answer(f"Событие '{event['name']}' удалено")
        await callback.message.edit_text("Выбери событие:", reply_markup=get_events_keyboard())
    else:
        await callback.answer("Событие не найдено")


async def main():
    db.init_db()
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
