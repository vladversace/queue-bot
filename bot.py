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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


class QueueStates(StatesGroup):
    waiting_for_position = State()
    waiting_for_event_name = State()
    waiting_for_max_positions = State()


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
        buttons.append([InlineKeyboardButton(text="🗑 Удалить событие", callback_data=f"delete_{event_id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_events")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
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
    data = await state.get_data()
    event_name = data["event_name"]
    
    try:
        max_pos = int(message.text) if message.text.strip() else 30
    except ValueError:
        max_pos = 30
    
    if db.add_event(event_name, max_pos):
        await message.answer(f"Событие '{event_name}' создано (макс. {max_pos} позиций)")
    else:
        await message.answer(f"Событие '{event_name}' уже существует")
    
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
    event_id = int(callback.data.split("_")[1])
    event = db.get_event_by_id(event_id)
    if not event:
        await callback.answer("Событие не найдено")
        return
    
    queue = db.get_queue(event_id)
    taken = len(queue)
    
    await callback.message.edit_text(
        f"📌 {event['name']}\n"
        f"Занято: {taken}/{event['max_positions']}",
        reply_markup=get_event_actions_keyboard(event_id, callback.from_user.id)
    )


@dp.callback_query(F.data.startswith("register_"))
async def callback_register(callback: CallbackQuery, state: FSMContext):
    event_id = int(callback.data.split("_")[1])
    await state.update_data(event_id=event_id)
    await state.set_state(QueueStates.waiting_for_position)
    
    event = db.get_event_by_id(event_id)
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
