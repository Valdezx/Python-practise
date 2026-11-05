import asyncio
import os
import asyncpg
from dotenv import load_dotenv
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

DB_CONFIG = {
    'user': os.getenv("DB_USER"),
    'password': os.getenv("DB_PASSWORD"),
    'database': os.getenv("DB_NAME"),
    'host': os.getenv("DB_HOST"),
    'port': int(os.getenv("DB_PORT", 5432))
}

bot = Bot(token=TOKEN)
dp = Dispatcher()

db_pool = None


# Стани для FSM
class EventState(StatesGroup):
    waiting_for_event_name = State()
    waiting_for_event_time = State()


class ListState(StatesGroup):
    waiting_for_list_name = State()
    waiting_for_list_item = State()
    waiting_for_new_item = State()  # Додавання пункту до існуючого списку


class NoteState(StatesGroup):
    waiting_for_note_text = State()  # Створення нотатки


# --- СЛОВНИК ПЕРЕКЛАДІВ ТА ТЕКСТІВ КНОПОК ---
translations = {
    "ua": {
        "welcome": "Привіт! Оберіть мову інтерфейсу за допомогою кнопок:",
        "lang_changed": "Мову успішно змінено на українську! 🇺🇦 Користуйтеся меню нижче 👇",
        "enter_event": "Введіть назву або опис події (або натисніть «Скасувати дію»):",
        "enter_time": "Введіть час події у форматі ДД.ММ.РРРР ГГ:ХХ:",
        "invalid_time": "Невірний формат дати та часу! Спробуйте ще раз:",
        "event_saved": "Подію успішно збережено! ✅",
        "name_list": "Введіть назву нового списку:",
        "enter_item": "Введіть черговий пункт для списку (або натисніть кнопку «Готово», щоб завершити):",
        "list_saved": "Список успішно створено та збережено! ✅",
        "enter_new_item": "Введіть новий пункт для цього списку:",
        "item_added": "Новий пункт успішно додано! ✅",
        "enter_note": "Введіть текст вашої нотатки:",
        "note_saved": "Нотатку успішно збережено! ✅",
        # Назви кнопок нижнього меню (UA)
        "btn_events": "📅 Мої події",
        "btn_lists": "📋 Мої списки",
        "btn_add_event": "➕ Додати подію",
        "btn_add_list": "➕ Створити список",
        "btn_notes": "📌 Мої нотатки",
        "btn_add_note": "➕ Створити нотатку",
        "btn_lang": "🌐 Змінити мову",
        "btn_cancel": "❌ Скасувати дію"
    },
    "en": {
        "welcome": "Hello! Choose your language using the buttons:",
        "lang_changed": "Language successfully changed! 🇬🇧 Use the menu below 👇",
        "enter_event": "Enter event name or description (or click 'Cancel'):",
        "enter_time": "Enter event time as DD.MM.YYYY HH:MM:",
        "invalid_time": "Invalid date format! Try again:",
        "event_saved": "Event successfully saved! ✅",
        "name_list": "Enter the name of the new list:",
        "enter_item": "Enter next item for the list (or click 'Done' to finish):",
        "list_saved": "List successfully created! ✅",
        "enter_new_item": "Enter new item for this list:",
        "item_added": "New item successfully added! ✅",
        "enter_note": "Enter your note text:",
        "note_saved": "Note successfully saved! ✅",
        # Назви кнопок нижнього меню (EN)
        "btn_events": "📅 My Events",
        "btn_lists": "📋 My Lists",
        "btn_add_event": "➕ Add Event",
        "btn_add_list": "➕ Create List",
        "btn_notes": "📌 My Notes",
        "btn_add_note": "➕ Create Note",
        "btn_lang": "🌐 Change Language",
        "btn_cancel": "❌ Cancel"
    }
}


# Динамічна функція для створення клавіатури залежно від мови
def get_main_keyboard(lang: str = "ua"):
    t = translations.get(lang, translations["ua"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t["btn_events"]), KeyboardButton(text=t["btn_lists"])],
            [KeyboardButton(text=t["btn_add_event"]), KeyboardButton(text=t["btn_add_list"])],
            [KeyboardButton(text=t["btn_notes"]), KeyboardButton(text=t["btn_add_note"])],
            [KeyboardButton(text=t["btn_lang"]), KeyboardButton(text=t["btn_cancel"])]
        ],
        resize_keyboard=True,
        input_field_placeholder="Оберіть дію з меню нижче..." if lang == "ua" else "Choose action from menu below..."
    )


# --- ІНІЦІАЛІЗАЦІЯ БАЗИ ДАНИХ ---
async def init_db():
    global db_pool

    temp_config = DB_CONFIG.copy()
    temp_config['database'] = 'postgres'

    conn = await asyncpg.connect(**temp_config)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", DB_CONFIG['database']
        )
        if not exists:
            await conn.execute(f"CREATE DATABASE {DB_CONFIG['database']}")
    finally:
        await conn.close()

    db_pool = await asyncpg.create_pool(**DB_CONFIG)

    async with db_pool.acquire() as connection:
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                language VARCHAR(10)
            )
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                event_name TEXT,
                event_time TIMESTAMP
            )
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS lists (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                list_name TEXT
            )
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS list_items (
                id SERIAL PRIMARY KEY,
                list_id INT REFERENCES lists(id) ON DELETE CASCADE,
                item_text TEXT
            )
        """)
        await connection.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                note_text TEXT
            )
        """)


# --- ФУНКЦІЇ РОБОТИ З ДАНИМИ ---
async def get_user_lang(user_id: int) -> str:
    async with db_pool.acquire() as connection:
        row = await connection.fetchrow("SELECT language FROM users WHERE user_id = $1", user_id)
        return row['language'] if row and row['language'] else "ua"


async def set_user_lang(user_id: int, lang: str):
    async with db_pool.acquire() as connection:
        await connection.execute("""
            INSERT INTO users (user_id, language) VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET language = $2
        """, user_id, lang)


# --- ФОНОВИЙ ПЛАНУВАЛЬНИК ---
async def check_reminders():
    while True:
        now = datetime.now()
        async with db_pool.acquire() as connection:
            rows = await connection.fetch("""
                SELECT id, user_id, event_name, event_time 
                FROM events 
                WHERE event_time <= $1
            """, now)

            for row in rows:
                event_id = row['id']
                user_id = row['user_id']
                event_name = row['event_name']

                builder = InlineKeyboardBuilder()
                builder.button(text="❌ Зупинити нагадування", callback_data=f"del_event_{event_id}")

                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=f"⏰ **Нагадування про подію!**\n\n📌 {event_name}",
                        reply_markup=builder.as_markup(),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print(f"Помилка нагадування: {e}")

                await connection.execute("DELETE FROM events WHERE id = $1", event_id)

        await asyncio.sleep(60)


# --- КОМАНДИ ТА КНОПКИ ГОЛОВНОГО МЕНЮ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🇺🇦 Українська", callback_data="lang_ua")
    builder.button(text="🇬🇧 English", callback_data="lang_en")
    builder.adjust(2)

    await message.answer("Оберіть мову / Choose your language:", reply_markup=builder.as_markup())


@dp.message(Command("language"))
async def cmd_language_cmd(message: types.Message):
    await send_language_menu(message)


@dp.message(F.text.in_({"🌐 Змінити мову", "🌐 Change Language"}))
async def cmd_language_txt(message: types.Message):
    await send_language_menu(message)


async def send_language_menu(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🇺🇦 Українська", callback_data="lang_ua")
    builder.button(text="🇬🇧 English", callback_data="lang_en")
    builder.adjust(2)
    await message.answer("Оберіть мову / Choose your language:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("lang_"))
async def process_lang_choice(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang_code = callback.data.split("_")[1]
    await set_user_lang(user_id, lang_code)

    await callback.message.answer(
        translations[lang_code]["lang_changed"],
        reply_markup=get_main_keyboard(lang_code)
    )
    await callback.answer()


# --- FSM ПОДІЇ ---
async def start_event_creation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)
    await message.answer(translations[lang]["enter_event"], reply_markup=get_main_keyboard(lang))
    await state.set_state(EventState.waiting_for_event_name)


@dp.message(Command("addevent"))
async def cmd_addevent_cmd(message: types.Message, state: FSMContext):
    await start_event_creation(message, state)


@dp.message(F.text.in_({"➕ Додати подію", "➕ Add Event"}))
async def cmd_addevent_txt(message: types.Message, state: FSMContext):
    await start_event_creation(message, state)


@dp.message(EventState.waiting_for_event_name)
async def process_event_name(message: types.Message, state: FSMContext):
    if message.text in ["❌ Скасувати дію", "❌ Cancel"]:
        await cmd_cancel_execution(message, state)
        return

    await state.update_data(event_name=message.text)
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)
    await message.answer(translations[lang]["enter_time"], reply_markup=get_main_keyboard(lang))
    await state.set_state(EventState.waiting_for_event_time)


@dp.message(EventState.waiting_for_event_time)
async def process_event_time(message: types.Message, state: FSMContext):
    if message.text in ["❌ Скасувати дію", "❌ Cancel"]:
        await cmd_cancel_execution(message, state)
        return

    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    try:
        event_time = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer(translations[lang]["invalid_time"], reply_markup=get_main_keyboard(lang))
        return

    data = await state.get_data()
    event_name = data.get("event_name")

    async with db_pool.acquire() as connection:
        await connection.execute("""
            INSERT INTO events (user_id, event_name, event_time) VALUES ($1, $2, $3)
        """, user_id, event_name, event_time)

    await message.answer(translations[lang]["event_saved"], reply_markup=get_main_keyboard(lang))
    await state.clear()


# --- FSM СПИСКИ З КІЛЬКОМА ПУНКТАМИ ---
async def start_list_creation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)
    await message.answer(translations[lang]["name_list"], reply_markup=get_main_keyboard(lang))
    await state.set_state(ListState.waiting_for_list_name)


@dp.message(Command("makelist"))
async def cmd_makelist_cmd(message: types.Message, state: FSMContext):
    await start_list_creation(message, state)


@dp.message(F.text.in_({"➕ Створити список", "➕ Create List"}))
async def cmd_makelist_txt(message: types.Message, state: FSMContext):
    await start_list_creation(message, state)


@dp.message(ListState.waiting_for_list_name)
async def process_list_name(message: types.Message, state: FSMContext):
    if message.text in ["❌ Скасувати дію", "❌ Cancel"]:
        await cmd_cancel_execution(message, state)
        return

    await state.update_data(list_name=message.text, items=[])
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Готово (Зберегти список)" if lang == "ua" else "✅ Done (Save List)", callback_data="finish_list")

    await message.answer(translations[lang]["enter_item"], reply_markup=builder.as_markup())
    await state.set_state(ListState.waiting_for_list_item)


@dp.message(ListState.waiting_for_list_item)
async def process_list_item(message: types.Message, state: FSMContext):
    if message.text in ["❌ Скасувати дію", "❌ Cancel"]:
        await cmd_cancel_execution(message, state)
        return

    data = await state.get_data()
    items = data.get("items", [])
    items.append(message.text)
    await state.update_data(items=items)

    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Готово (Зберегти список)" if lang == "ua" else "✅ Done (Save List)", callback_data="finish_list")

    msg_text = "Пункт додано! Введіть наступний або натисніть «Готово»:" if lang == "ua" else "Item added! Enter next or click 'Done':"
    await message.answer(msg_text, reply_markup=builder.as_markup())


@dp.callback_query(F.data == "finish_list")
async def finish_list_creation(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    list_name = data.get("list_name")
    items = data.get("items", [])
    user_id = callback.from_user.id
    lang = await get_user_lang(user_id)

    if not items:
        err_msg = "Список порожній! Додайте хоча б один пункт." if lang == "ua" else "List is empty! Add at least one item."
        await callback.answer(err_msg, show_alert=True)
        return

    async with db_pool.acquire() as connection:
        list_id = await connection.fetchval("""
            INSERT INTO lists (user_id, list_name) VALUES ($1, $2) RETURNING id
        """, user_id, list_name)

        for item in items:
            await connection.execute("""
                INSERT INTO list_items (list_id, item_text) VALUES ($1, $2)
            """, list_id, item)

    await callback.message.answer(translations[lang]["list_saved"], reply_markup=get_main_keyboard(lang))
    await callback.answer()
    await state.clear()


# --- ПЕРЕГЛЯД ТА КЕРУВАННЯ СПИСКАМИ ---
async def show_user_lists(message: types.Message):
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        lists = await connection.fetch("SELECT id, list_name FROM lists WHERE user_id = $1", user_id)

    if not lists:
        msg = "У вас немає списків. Використовуйте кнопку нижче." if lang == "ua" else "No lists found."
        await message.answer(msg, reply_markup=get_main_keyboard(lang))
        return

    builder = InlineKeyboardBuilder()
    for lst in lists:
        builder.button(text=f"📋 {lst['list_name']}", callback_data=f"view_list_{lst['id']}")

    builder.adjust(1)
    text = "Оберіть список для перегляду та керування:" if lang == "ua" else "Select a list to view and manage:"
    await message.answer(text, reply_markup=builder.as_markup())


@dp.message(Command("mylists"))
async def cmd_mylists_cmd(message: types.Message):
    await show_user_lists(message)


@dp.message(F.text.in_({"📋 Мої списки", "📋 My Lists"}))
async def cmd_mylists_txt(message: types.Message):
    await show_user_lists(message)


@dp.callback_query(F.data.startswith("view_list_"))
async def view_single_list(callback: types.CallbackQuery, state: FSMContext):
    list_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang = await get_user_lang(user_id)

    # Запам'ятовуємо активний список у FSM
    await state.update_data(active_list_id=list_id)

    async with db_pool.acquire() as connection:
        lst = await connection.fetchrow("SELECT list_name FROM lists WHERE id = $1", list_id)
        items = await connection.fetch("SELECT id, item_text FROM list_items WHERE list_id = $1", list_id)

    if not lst:
        await callback.answer("Список не знайдено." if lang == "ua" else "List not found.", show_alert=True)
        return

    text = f"📋 **{lst['list_name']}**\n\n"
    builder = InlineKeyboardBuilder()

    del_item_text = "❌ Видалити: " if lang == "ua" else "❌ Delete: "
    for item in items:
        text += f"• {item['item_text']}\n"
        builder.button(text=f"{del_item_text}{item['item_text'][:10]}", callback_data=f"del_item_{item['id']}")

    add_btn_text = "➕ Додати пункт до списку" if lang == "ua" else "➕ Add item to list"
    del_list_text = "🗑 Видалити весь список" if lang == "ua" else "🗑 Delete entire list"
    back_text = "⬅️ Назад до списків" if lang == "ua" else "⬅️ Back to lists"

    builder.button(text=add_btn_text, callback_data=f"add_to_list_{list_id}")
    builder.button(text=del_list_text, callback_data=f"del_list_{list_id}")
    builder.button(text=back_text, callback_data="back_to_lists")
    builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


@dp.callback_query(F.data.startswith("add_to_list_"))
async def prompt_add_to_list(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    lang = await get_user_lang(user_id)

    await callback.message.answer(translations[lang]["enter_new_item"], reply_markup=get_main_keyboard(lang))
    await state.set_state(ListState.waiting_for_new_item)
    await callback.answer()


@dp.message(ListState.waiting_for_new_item)
async def save_new_list_item(message: types.Message, state: FSMContext):
    if message.text in ["❌ Скасувати дію", "❌ Cancel"]:
        await cmd_cancel_execution(message, state)
        return

    data = await state.get_data()
    list_id = data.get("active_list_id")
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        await connection.execute("""
            INSERT INTO list_items (list_id, item_text) VALUES ($1, $2)
        """, list_id, message.text)

    await message.answer(translations[lang]["item_added"], reply_markup=get_main_keyboard(lang))
    await state.clear()


@dp.callback_query(F.data.startswith("del_item_"))
async def delete_list_item(callback: types.CallbackQuery):
    item_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        list_id = await connection.fetchval("SELECT list_id FROM list_items WHERE id = $1", item_id)
        await connection.execute("DELETE FROM list_items WHERE id = $1", item_id)

    await callback.answer("Пункт видалено!" if lang == "ua" else "Item deleted!")

    if list_id:
        callback.data = f"view_list_{list_id}"
        await view_single_list(callback, FSMContext(None, None))
    else:
        await callback.message.edit_text("Пункт видалено." if lang == "ua" else "Item deleted.")


@dp.callback_query(F.data.startswith("del_list_"))
async def process_delete_list(callback: types.CallbackQuery):
    list_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        await connection.execute("DELETE FROM lists WHERE id = $1 AND user_id = $2", list_id, user_id)

    msg = "Список успішно видалено! 🗑" if lang == "ua" else "List successfully deleted! 🗑"
    await callback.answer(msg)
    await callback.message.edit_text("Список повністю видалено." if lang == "ua" else "List completely deleted.")


@dp.callback_query(F.data == "back_to_lists")
async def back_to_lists(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = await get_user_lang(user_id)
    async with db_pool.acquire() as connection:
        lists = await connection.fetch("SELECT id, list_name FROM lists WHERE user_id = $1", user_id)

    builder = InlineKeyboardBuilder()
    for lst in lists:
        builder.button(text=f"📋 {lst['list_name']}", callback_data=f"view_list_{lst['id']}")

    builder.adjust(1)
    text = "Оберіть список для перегляду та керування:" if lang == "ua" else "Select a list to view and manage:"
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


# --- НОТАТКИ (NOTES) ---
@dp.message(F.text.in_({"➕ Створити нотатку", "➕ Create Note"}))
async def cmd_add_note(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)
    await message.answer(translations[lang]["enter_note"], reply_markup=get_main_keyboard(lang))
    await state.set_state(NoteState.waiting_for_note_text)


@dp.message(NoteState.waiting_for_note_text)
async def process_note_text(message: types.Message, state: FSMContext):
    if message.text in ["❌ Скасувати дію", "❌ Cancel"]:
        await cmd_cancel_execution(message, state)
        return

    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        await connection.execute("""
            INSERT INTO notes (user_id, note_text) VALUES ($1, $2)
        """, user_id, message.text)

    await message.answer(translations[lang]["note_saved"], reply_markup=get_main_keyboard(lang))
    await state.clear()


@dp.message(F.text.in_({"📌 Мої нотатки", "📌 My Notes"}))
async def cmd_my_notes(message: types.Message):
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        notes = await connection.fetch("SELECT id, note_text FROM notes WHERE user_id = $1", user_id)

    if not notes:
        msg = "У вас немає нотаток." if lang == "ua" else "You have no notes."
        await message.answer(msg, reply_markup=get_main_keyboard(lang))
        return

    builder = InlineKeyboardBuilder()
    text = "📌 **Ваші нотатки:**\n\n" if lang == "ua" else "📌 **Your notes:**\n\n"

    del_note_text = "🗑 Видалити: " if lang == "ua" else "🗑 Delete: "
    for note in notes:
        snippet = note['note_text'][:20]
        text += f"• {snippet}...\n"
        builder.button(text=f"{del_note_text}{snippet}", callback_data=f"del_note_{note['id']}")

    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("del_note_"))
async def delete_note(callback: types.CallbackQuery):
    note_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        await connection.execute("DELETE FROM notes WHERE id = $1 AND user_id = $2", note_id, user_id)

    await callback.answer("Нотатку видалено!" if lang == "ua" else "Note deleted!")
    await callback.message.edit_text("Нотатку успішно видалено." if lang == "ua" else "Note successfully deleted.")


# --- УПРАВЛІННЯ ПОДІЯМИ ---
async def show_user_events(message: types.Message):
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        rows = await connection.fetch("""
            SELECT id, event_name, event_time 
            FROM events 
            WHERE user_id = $1 AND event_time >= $2
            ORDER BY event_time ASC
        """, user_id, datetime.now())

    if not rows:
        msg = "У вас немає запланованих подій." if lang == "ua" else "No scheduled events."
        await message.answer(msg, reply_markup=get_main_keyboard(lang))
        return

    builder = InlineKeyboardBuilder()
    text = "📅 **Ваші активні події:**\n\n" if lang == "ua" else "📅 **Your active events:**\n\n"

    del_text = "🗑 Видалити: " if lang == "ua" else "🗑 Delete: "
    for row in rows:
        event_id = row['id']
        event_name = row['event_name']
        event_time_str = row['event_time'].strftime('%d.%m.%Y о %H:%M')
        text += f"• **{event_name}** — ⏰ *{event_time_str}*\n"
        builder.button(text=f"{del_text}{event_name[:15]}", callback_data=f"del_event_{event_id}")

    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.message(Command("myevents"))
async def cmd_myevents_cmd(message: types.Message):
    await show_user_events(message)


@dp.message(F.text.in_({"📅 Мої події", "📅 My Events"}))
async def cmd_myevents_txt(message: types.Message):
    await show_user_events(message)


@dp.callback_query(F.data.startswith("del_event_"))
async def process_delete_event(callback: types.CallbackQuery):
    event_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    lang = await get_user_lang(user_id)

    async with db_pool.acquire() as connection:
        await connection.execute("DELETE FROM events WHERE id = $1 AND user_id = $2", event_id, user_id)

    msg = "Подію успішно видалено! 🗑" if lang == "ua" else "Event successfully deleted! 🗑"
    await callback.answer(msg)
    await callback.message.edit_text("Подію видалено." if lang == "ua" else "Event deleted.")


# --- СКАСУВАННЯ ---
async def cmd_cancel_execution(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang = await get_user_lang(user_id)

    current_state = await state.get_state()
    if current_state is None:
        msg = "Немає активних процесів для скасування." if lang == "ua" else "No active processes."
        await message.answer(msg, reply_markup=get_main_keyboard(lang))
        return

    await state.clear()
    msg = "Дію успішно скасовано! ❌" if lang == "ua" else "Action cancelled! ❌"
    await message.answer(msg, reply_markup=get_main_keyboard(lang))


@dp.message(Command("cancel"))
async def cmd_cancel_cmd(message: types.Message, state: FSMContext):
    await cmd_cancel_execution(message, state)


@dp.message(F.text.in_({"❌ Скасувати дію", "❌ Cancel"}))
async def cmd_cancel_txt(message: types.Message, state: FSMContext):
    await cmd_cancel_execution(message, state)


async def main():
    await init_db()
    asyncio.create_task(check_reminders())
    print("Bot started with PostgreSQL support, dynamic keyboards & Notes!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())