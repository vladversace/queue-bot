import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import database as db

# Configure logging
logging.basicConfig(level=logging.INFO)

# Bot token from environment variable
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
ALLOWED_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_IDS", "").split(",") if x.strip().isdigit()]
SUBGROUP1_IDS = [int(x.strip()) for x in os.getenv("SUBGROUP1_IDS", "").split(",") if x.strip().isdigit()]
SUBGROUP2_IDS = [int(x.strip()) for x in os.getenv("SUBGROUP2_IDS", "").split(",") if x.strip().isdigit()]
FORUM_CHAT_ID = int(os.getenv("FORUM_CHAT_ID", "0"))
FORUM_THREAD_ID = int(os.getenv("FORUM_THREAD_ID", "0"))

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
    
    admin_note = " (ты админ)" if is_admin(message.from_user.id) else ""
    await message.answer(
        f"Бот для записи в очередь на сдачу работ.{admin_note}\n\n"
        "Команды:\n"
        "/events — список событий\n"
        "/dashboard — ссылка на дашборд",
        reply_markup=get_events_keyboard()
    )


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
            
            # Получаем ID созданного события
            events = db.get_events()
            event_id = None
            for e in events:
                if e["name"] == event_name:
                    event_id = e["id"]
                    break
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📝 Записаться",
                    url=f"https://t.me/Queue521701_bot?start=register_{event_id}"
                )]
            ])
            
            try:
                await bot.send_message(
                    chat_id=FORUM_CHAT_ID,
                    message_thread_id=FORUM_THREAD_ID,
                    text=f"📢 Новое событие: {event_name}\n"
                         f"Мест: {max_pos}{subgroup_text}",
                    reply_markup=keyboard
                )
            except Exception:
                pass
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
        await callback.answer(f"Событие '{event['name']}' удалено")
        await callback.message.edit_text("Выбери событие:", reply_markup=get_events_keyboard())
    else:
        await callback.answer("Событие не найдено")


async def main():
    db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
