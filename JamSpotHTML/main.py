import os
import sys
import json
import re
import uuid
import sqlite3
import hashlib
import asyncio
from dotenv import load_dotenv
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ContentType, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils import executor

#NOTE - функции в тг работают, а не сайте то работают то нет - это весьма загадочно... 

#! Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = ('#ANCHOR - нужен тг токен') or (sys.argv[1] if len(sys.argv) > 1 else None)
ADMIN_ID = ('#ANCHOR - нужен айди для админа')
if not BOT_TOKEN or not ADMIN_ID:
    print("Error: provide BOT_TOKEN and ADMIN_ID")
    sys.exit(1)

#! Определение путей
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'jamspot.db')
PROJECTS_JSON = os.path.join(BASE_DIR, 'projects.json')
TEAMS_JSON = os.path.join(BASE_DIR, 'teams.json')
JAMS_JSON = os.path.join(BASE_DIR, 'jams.json')

os.makedirs(UPLOAD_DIR, exist_ok=True)

#! Инициализация базы данных
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.executescript('''
CREATE TABLE IF NOT EXISTS games(
    token TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    photo_path TEXT NOT NULL,
    zip_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teams(
    team_name TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members(
    team_name TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY(team_name,user_id)
);
CREATE TABLE IF NOT EXISTS jam_requests(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    conditions TEXT,
    reward TEXT,
    participants INTEGER,
    contact TEXT,
    requester_id INTEGER
);
CREATE TABLE IF NOT EXISTS jams(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    conditions TEXT,
    reward TEXT,
    participants INTEGER,
    contact TEXT
);
''')
conn.commit()

#! Функции генерации JSON
def regenerate_projects_json():
    cursor.execute("DELETE FROM games WHERE LENGTH(TRIM(name)) < 3")
    bad = [(t,) for t, p, z in cursor.execute("SELECT token, photo_path, zip_path FROM games") 
           if not (os.path.exists(p) and os.path.exists(z))]
    if bad:
        cursor.executemany("DELETE FROM games WHERE token=?", bad)
        conn.commit()
    
    projects = []
    for t, n, o, p, z in cursor.execute("SELECT token, name, owner_id, photo_path, zip_path FROM games"):
        projects.append({
            'token': t,
            'name': n,
            'owner_id': o,
            'photo': os.path.relpath(p, BASE_DIR).replace('\\', '/'),
            'zip': os.path.relpath(z, BASE_DIR).replace('\\', '/')
        })
    
    with open(PROJECTS_JSON, 'w', encoding='utf-8') as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)

def regenerate_teams_json():
    teams = []
    for tn, o, pw in cursor.execute("SELECT team_name, owner_id, password_hash FROM teams"):
        members = [r[0] for r in cursor.execute(
            "SELECT user_id FROM team_members WHERE team_name=?", (tn,))]
        teams.append({
            'team_name': tn,
            'owner_id': o,
            'password_hash': pw,
            'members': members
        })
    
    with open(TEAMS_JSON, 'w', encoding='utf-8') as f:
        json.dump(teams, f, ensure_ascii=False, indent=2)

def regenerate_jams_json():
    jams = []
    for row in cursor.execute("SELECT id, title, conditions, reward, participants, contact FROM jams"):
        jams.append({
            'id': row[0],
            'title': row[1],
            'conditions': row[2],
            'reward': row[3],
            'participants': row[4],
            'contact': row[5]
        })
    
    with open(JAMS_JSON, 'w', encoding='utf-8') as f:
        json.dump(jams, f, ensure_ascii=False, indent=2)

regenerate_projects_json()
regenerate_teams_json()
regenerate_jams_json()

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# Состояния FSM
class ProjectStates(StatesGroup):
    name = State()
    photo = State()
    zipfile = State()

class TeamStates(StatesGroup):
    name = State()
    pwd = State()

class JamStates(StatesGroup):
    title = State()
    conditions = State()
    reward = State()
    participants = State()
    contact = State()

# Текст помощи
HELP_TEXT = (
    "/register - Зарегистрировать проект\n"
    "/get <token> - Скачать проект\n"
    "/create_team - Создать команду\n"
    "/add_member <team> <@user> - Добавить участника\n"
    "/my_team - Мои команды\n"
    "/team_projects <team> - Проекты команды\n"
    "/delete_project <token> - Удалить проект\n"
    "/create_jam - Создать джем\n"
    "/refresh - Обновить JSON\n"
    "/help - Помощь"
)

@dp.message_handler(commands=['start', 'help'])
async def cmd_help(message: types.Message):
    await message.reply(HELP_TEXT)

@dp.message_handler(commands=['refresh'])
async def cmd_refresh(message: types.Message):
    regenerate_projects_json()
    regenerate_teams_json()
    regenerate_jams_json()
    await message.reply("JSON файлы обновлены")

# Обработчики проектов
@dp.message_handler(commands=['register'])
async def reg1(message: types.Message):
    await message.reply("Введите название проекта (мин. 3 символа):")
    await ProjectStates.name.set()

@dp.message_handler(state=ProjectStates.name, content_types=ContentType.TEXT)
async def reg2(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 3:
        return await message.reply("Слишком короткое название")
    await state.update_data(name=name)
    await message.reply("Отправьте превью-изображение:")
    await ProjectStates.photo.set()

@dp.message_handler(state=ProjectStates.photo, content_types=ContentType.PHOTO)
async def reg3(message: types.Message, state: FSMContext):
    try:
        photo_tmp = os.path.join(UPLOAD_DIR, f"temp_{uuid.uuid4().hex[:8]}.jpg")
        await message.photo[-1].download(destination_file=photo_tmp)
        await state.update_data(photo=photo_tmp)
        await message.reply("Отправьте ZIP-архив:")
        await ProjectStates.zipfile.set()
    except Exception as e:
        await message.reply(f"Ошибка загрузки фото: {str(e)}")
        await state.finish()

@dp.message_handler(state=ProjectStates.zipfile, content_types=ContentType.DOCUMENT)
async def reg4(message: types.Message, state: FSMContext):
    try:
        if not message.document.file_name.lower().endswith('.zip'):
            return await message.reply("Требуется ZIP-архив")

        data = await state.get_data()
        name = data['name']
        photo_tmp = data['photo']
        token = uuid.uuid4().hex[:8]
        slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
        proj_dir = os.path.join(UPLOAD_DIR, f"{slug}_{token}")
        
        os.makedirs(proj_dir, exist_ok=True)

        #! Перемещение фото
        ext = os.path.splitext(photo_tmp)[1]
        photo_path = os.path.join(proj_dir, f"{token}_preview{ext}")
        os.replace(photo_tmp, photo_path)

        #! Сохранение ZIP
        zip_path = os.path.join(proj_dir, f"{token}.zip")
        await message.document.download(destination_file=zip_path)

        #! Запись в БД
        cursor.execute(
            "INSERT INTO games(token, name, owner_id, photo_path, zip_path) VALUES(?,?,?,?,?)",
            (token, name, message.from_user.id, photo_path, zip_path)
        )
        conn.commit()
        regenerate_projects_json()
        await message.reply(f"Проект зарегистрирован! Токен: {token}")
        
    except Exception as e:
        await message.reply(f"Ошибка: {str(e)}")
        if 'photo_tmp' in locals() and os.path.exists(photo_tmp):
            os.remove(photo_tmp)
    finally:
        await state.finish()

@dp.message_handler(commands=['get'])
async def cmd_get(message: types.Message):
    token = message.get_args().strip()
    row = cursor.execute("SELECT zip_path FROM games WHERE token=?", (token,)).fetchone()
    if not row:
        return await message.reply("Токен не найден")
    await message.reply_document(open(row[0], 'rb'))

#! Обработчики команд
@dp.message_handler(commands=['create_team'])
async def tm1(message: types.Message):
    await message.reply("Введите название команды:")
    await TeamStates.name.set()

@dp.message_handler(state=TeamStates.name, content_types=ContentType.TEXT)
async def tm2(message: types.Message, state: FSMContext):
    tn = message.text.strip()
    if cursor.execute("SELECT 1 FROM teams WHERE team_name=?", (tn,)).fetchone():
        await state.finish()
        return await message.reply("Команда уже существует")
    await state.update_data(name=tn)
    await message.reply("Установите пароль (мин. 6 символов):")
    await TeamStates.pwd.set()

@dp.message_handler(state=TeamStates.pwd, content_types=ContentType.TEXT)
async def tm3(message: types.Message, state: FSMContext):
    pw = message.text.strip()
    if len(pw) < 6:
        return await message.reply("Слишком короткий пароль")
    data = await state.get_data()
    pwd_hash = hashlib.sha256(pw.encode()).hexdigest()
    
    try:
        cursor.execute(
            "INSERT INTO teams(team_name, owner_id, password_hash) VALUES(?,?,?)",
            (data['name'], message.from_user.id, pwd_hash)
        )
        cursor.execute(
            "INSERT INTO team_members(team_name, user_id) VALUES(?,?)",
            (data['name'], message.from_user.id)
        )
        conn.commit()
        regenerate_teams_json()
        await message.reply(f"Команда '{data['name']}' создана")
    except sqlite3.IntegrityError:
        await message.reply("Ошибка: такая команда уже существует")
    finally:
        await state.finish()

@dp.message_handler(commands=['add_member'])
async def cmd_add_member(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("Использование: /add_member <команда> @юзер")
    tn, mention = parts[1], parts[2]
    owner = cursor.execute("SELECT owner_id FROM teams WHERE team_name=?", (tn,)).fetchone()
    if not owner or owner[0] != message.from_user.id:
        return await message.reply("Только владелец может добавлять участников")
    uid = int(re.sub(r'\D', '', mention))
    cursor.execute("INSERT OR IGNORE INTO team_members(team_name, user_id) VALUES(?,?)", (tn, uid))
    conn.commit()
    regenerate_teams_json()
    await message.reply(f"{mention} добавлен в '{tn}'")

@dp.message_handler(commands=['my_team'])
async def cmd_my_team(message: types.Message):
    uid = message.from_user.id
    rows = cursor.execute("SELECT team_name FROM team_members WHERE user_id=?", (uid,)).fetchall()
    if not rows:
        return await message.reply("Вы не состоите в командах")
    await message.reply("Ваши команды: " + ", ".join(r[0] for r in rows))

@dp.message_handler(commands=['team_projects'])
async def cmd_team_projects(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Использование: /team_projects <команда>")
    tn = parts[1].strip()
    uid = message.from_user.id
    if not cursor.execute("SELECT 1 FROM team_members WHERE team_name=? AND user_id=?", (tn, uid)).fetchone():
        return await message.reply("Вы не состоите в этой команде")
    rows = cursor.execute(
        "SELECT token, name FROM games WHERE owner_id IN (SELECT user_id FROM team_members WHERE team_name=?)", (tn,)
    ).fetchall()
    if not rows:
        return await message.reply("Нет проектов в команде")
    await message.reply("Проекты: " + ", ".join(f"{t}: {n}" for t, n in rows))

@dp.message_handler(commands=['delete_project'])
async def cmd_delete_project(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Использование: /delete_project <токен>")
    tk = parts[1].strip()
    row = cursor.execute("SELECT photo_path, zip_path, owner_id FROM games WHERE token=?", (tk,)).fetchone()
    if not row:
        return await message.reply("Проект не найден")
    if message.from_user.id != row[2]:
        return await message.reply("Только владелец может удалить проект")
    try:
        os.remove(row[0])
        os.remove(row[1])
        os.rmdir(os.path.dirname(row[0]))
    except Exception as e:
        pass
    cursor.execute("DELETE FROM games WHERE token=?", (tk,))
    conn.commit()
    regenerate_projects_json()
    await message.reply(f"Проект {tk} удалён")

# Обработчики джемов
@dp.message_handler(commands=['create_jam'])
async def jam1(message: types.Message):
    await message.reply("Введите название джема:")
    await JamStates.title.set()

@dp.message_handler(state=JamStates.title, content_types=ContentType.TEXT)
async def jam2(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.reply("Введите условия:")
    await JamStates.conditions.set()

@dp.message_handler(state=JamStates.conditions, content_types=ContentType.TEXT)
async def jam3(message: types.Message, state: FSMContext):
    await state.update_data(conditions=message.text.strip())
    await message.reply("Введите награду (опционально):")
    await JamStates.reward.set()

@dp.message_handler(state=JamStates.reward, content_types=ContentType.TEXT)
async def jam4(message: types.Message, state: FSMContext):
    await state.update_data(reward=message.text.strip())
    await message.reply("Введите максимальное количество участников:")
    await JamStates.participants.set()

@dp.message_handler(state=JamStates.participants, content_types=ContentType.TEXT)
async def jam5(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.reply("Введите число")
    await state.update_data(participants=int(message.text))
    await message.reply("Введите контактную информацию:")
    await JamStates.contact.set()

@dp.message_handler(state=JamStates.contact, content_types=ContentType.TEXT)
async def jam6(message: types.Message, state: FSMContext):
    data = await state.get_data()
    contact = message.text.strip()
    try:
        cursor.execute(
            "INSERT INTO jam_requests(title, conditions, reward, participants, contact, requester_id) VALUES(?,?,?,?,?,?)",
            (data['title'], data['conditions'], data['reward'], data['participants'], contact, message.from_user.id)
        )
        req_id = cursor.lastrowid
        conn.commit()
        text = (
            f"Запрос на джем #{req_id}:\n"
            f"Название: {data['title']}\n"
            f"Условия: {data['conditions']}\n"
            f"Награда: {data['reward']}\n"
            f"Участники: {data['participants']}\n"
            f"Контакт: {contact}\n"
            f"От: @{message.from_user.username or message.from_user.id}"
        )
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton('✅ Одобрить', callback_data=f"approve_jam:{req_id}"),
            InlineKeyboardButton('❌ Отклонить', callback_data=f"reject_jam:{req_id}")
        )
        await bot.send_message(ADMIN_ID, text, reply_markup=kb)
        await message.reply("Запрос отправлен на модерацию")
    except Exception as e:
        await message.reply(f"Ошибка: {str(e)}")
    finally:
        await state.finish()

# Колбэки для джемов
@dp.callback_query_handler(lambda c: c.data.startswith('approve_jam:'))
async def approve_jam(call: types.CallbackQuery):
    _, rid = call.data.split(':', 1)
    try:
        row = cursor.execute(
            "SELECT title, conditions, reward, participants, contact FROM jam_requests WHERE id=?", 
            (rid,)
        ).fetchone()
        if row:
            cursor.execute(
                "INSERT INTO jams(title, conditions, reward, participants, contact) VALUES(?,?,?,?,?)", 
                row
            )
            cursor.execute("DELETE FROM jam_requests WHERE id=?", (rid,))
            conn.commit()
            regenerate_jams_json()
            await call.message.edit_text(call.message.text + "\n\n✅ Одобрено")
    except Exception as e:
        print(f"Error approving jam: {str(e)}")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('reject_jam:'))
async def reject_jam(call: types.CallbackQuery):
    _, rid = call.data.split(':', 1)
    try:
        cursor.execute("DELETE FROM jam_requests WHERE id=?", (rid,))
        conn.commit()
        await call.message.edit_text(call.message.text + "\n\n❌ Отклонено")
    except Exception as e:
        print(f"Error rejecting jam: {str(e)}")
    await call.answer()

#! Веб-сервер
app = web.Application()
app.router.add_static('/', BASE_DIR)

async def start_web():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(start_web())
    executor.start_polling(dp, skip_updates=True)