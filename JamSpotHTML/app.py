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

# ЗАГРУЗКА
load_dotenv()
BOT_TOKEN = ('7474555208:AAHIyMB5-9MgFi_BWwHqAM6uDqAAdoTLOtI') or (sys.argv[1] if len(sys.argv)>1 else None)
ADMIN_ID  = ('1261986345')
if not BOT_TOKEN or not ADMIN_ID:
    print("Error: provide BOT_TOKEN and ADMIN_ID")
    sys.exit(1)

# Истинный путь
BASE_DIR      = os.getcwd()
UPLOAD_DIR    = os.path.join(BASE_DIR,'uploads')
DB_PATH       = os.path.join(BASE_DIR,'jamspot.db')
PROJECTS_JSON = os.path.join(BASE_DIR,'projects.json')
TEAMS_JSON    = os.path.join(BASE_DIR,'teams.json')
JAMS_JSON     = os.path.join(BASE_DIR,'jams.json')

os.makedirs(UPLOAD_DIR,exist_ok=True)

# Устоновка датабаз
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

# JSON регенирация
def regenerate_projects_json():
    cursor.execute("DELETE FROM games WHERE LENGTH(TRIM(name))<3")
    bad = [(t,) for t,p,z in cursor.execute("SELECT token,photo_path,zip_path FROM games")
           if not (os.path.exists(p) and os.path.exists(z))]
    if bad:
        cursor.executemany("DELETE FROM games WHERE token=?", bad)
        conn.commit()
    projects = [
        {'token': t, 'name': n, 'owner_id': o,
         'photo': os.path.relpath(p, BASE_DIR).replace('\\','/'),
         'zip':   os.path.relpath(z, BASE_DIR).replace('\\','/')}
        for t,n,o,p,z in cursor.execute(
            "SELECT token,name,owner_id,photo_path,zip_path FROM games")
    ]
    with open(PROJECTS_JSON, 'w', encoding='utf-8') as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)

def regenerate_teams_json():
    teams = []
    for tn, o, pw in cursor.execute(
        "SELECT team_name,owner_id,password_hash FROM teams"):
        members = [r[0] for r in cursor.execute(
            "SELECT user_id FROM team_members WHERE team_name=?", (tn,))]
        teams.append({'team_name': tn, 'owner_id': o, 'password_hash': pw, 'members': members})
    with open(TEAMS_JSON, 'w', encoding='utf-8') as f:
        json.dump(teams, f, ensure_ascii=False, indent=2)

def regenerate_jams_json():
    jams = [
        {'id': i, 'title': t, 'conditions': c, 'reward': r, 'participants': pr, 'contact': ct}
        for i,t,c,r,pr,ct in cursor.execute(
            "SELECT id,title,conditions,reward,participants,contact FROM jams")
    ]
    with open(JAMS_JSON, 'w', encoding='utf-8') as f:
        json.dump(jams, f, ensure_ascii=False, indent=2)

# Усоновить JSON регенирацию
regenerate_projects_json()
regenerate_teams_json()
regenerate_jams_json()

# Бот и диспетчер
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# FSM States
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

# Вспомогательный текст
HELP_TEXT = (
    "/register - зарегистрировать проект\n"
    "/get <token> - скачать проект\n"
    "/create_team - создать команду\n"
    "/add_member <team> <@user> - добавить пользователя в команду\n"
    "/my_team - список участников команды\n"
    "/team_projects <team> - списое проектов команды\n"
    "/delete_project <token> - удалить проект\n"
    "/create_jam - зарегистрировать JAM\n"
    "/refresh - перезагрузить JSON\n"
    "/help - показать это сообщение снова"
)

@dp.message_handler(commands=['start','help'])
async def cmd_help(message: types.Message):
    await message.reply(HELP_TEXT)

@dp.message_handler(commands=['refresh'])
async def cmd_refresh(message: types.Message):
    regenerate_projects_json()
    regenerate_teams_json()
    regenerate_jams_json()
    await message.reply("All JSON files refreshed.")

# Project registration handlers
@dp.message_handler(commands=['register'])
async def reg1(message: types.Message):
    await message.reply("Enter project name (min 3 chars):")
    await ProjectStates.name.set()

@dp.message_handler(state=ProjectStates.name, content_types=ContentType.TEXT)
async def reg2(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 3:
        return await message.reply("Name too short.")
    await state.update_data(name=name)
    await message.reply("Send preview image:")
    await ProjectStates.photo.set()

@dp.message_handler(state=ProjectStates.photo, content_types=ContentType.PHOTO)
async def reg3(message: types.Message, state: FSMContext):
    path = os.path.join(UPLOAD_DIR, f"temp_{uuid.uuid4().hex[:8]}.jpg")
    await message.photo[-1].download(destination_file=path)
    await state.update_data(photo=path)
    await message.reply("Send ZIP archive:")
    await ProjectStates.zipfile.set()

@dp.message_handler(state=ProjectStates.zipfile, content_types=ContentType.DOCUMENT)
async def reg4(message: types.Message, state: FSMContext):
    if not message.document.file_name.lower().endswith('.zip'):
        return await message.reply("Error: must be a ZIP.")
    data = await state.get_data()
    name = data['name']
    photo_tmp = data['photo']
    token = uuid.uuid4().hex[:8]
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower())
    proj_dir = os.path.join(UPLOAD_DIR, f"{slug}_{token}")
    os.makedirs(proj_dir, exist_ok=True)
    ext = os.path.splitext(photo_tmp)[1]
    photo_path = os.path.join(proj_dir, f"{token}_preview{ext}")
    os.replace(photo_tmp, photo_path)
    zip_path = os.path.join(proj_dir, f"{token}.zip")
    await message.document.download(destination_file=zip_path)
    cursor.execute(
        "INSERT INTO games(token,name,owner_id,photo_path,zip_path) VALUES(?,?,?,?,?)",
        (token, name, message.from_user.id, photo_path, zip_path)
    )
    conn.commit()
    regenerate_projects_json()
    await message.reply(f"Project registered! Token: {token}")
    await state.finish()

@dp.message_handler(commands=['get'])
async def cmd_get(message: types.Message):
    token = message.get_args().strip()
    row = cursor.execute("SELECT zip_path FROM games WHERE token=?", (token,)).fetchone()
    if not row:
        return await message.reply("Token not found.")
    await message.reply_document(open(row[0], 'rb'))

# Team creation handlers
@dp.message_handler(commands=['create_team'])
async def tm1(message: types.Message):
    await message.reply("Enter team name:")
    await TeamStates.name.set()

@dp.message_handler(state=TeamStates.name, content_types=ContentType.TEXT)
async def tm2(message: types.Message, state: FSMContext):
    tn = message.text.strip()
    if cursor.execute("SELECT 1 FROM teams WHERE team_name=?", (tn,)).fetchone():
        await state.finish()
        return await message.reply("Team already exists.")
    await state.update_data(name=tn)
    await message.reply("Set password (min 6 chars):")
    await TeamStates.pwd.set()

@dp.message_handler(state=TeamStates.pwd, content_types=ContentType.TEXT)
async def tm3(message: types.Message, state: FSMContext):
    pw = message.text.strip()
    if len(pw) < 6:
        return await message.reply("Password too short.")
    data = await state.get_data()
    pwd_hash = hashlib.sha256(pw.encode()).hexdigest()
    cursor.execute(
        "INSERT INTO teams(team_name,owner_id,password_hash) VALUES(?,?,?)",
        (data['name'], message.from_user.id, pwd_hash)
    )
    cursor.execute("INSERT INTO team_members(team_name,user_id) VALUES(?,?)",
                   (data['name'], message.from_user.id))
    conn.commit()
    regenerate_teams_json()
    await message.reply(f"Team '{data['name']}' created.")
    await state.finish()

@dp.message_handler(commands=['add_member'])
async def cmd_add_member(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("Usage: /add_member <team> <@user>")
    tn, mention = parts[1], parts[2]
    owner = cursor.execute("SELECT owner_id FROM teams WHERE team_name=?", (tn,)).fetchone()
    if not owner or owner[0] != message.from_user.id:
        return await message.reply("Only the owner can add members.")
    uid = int(re.sub(r'\D','',mention))
    cursor.execute("INSERT OR IGNORE INTO team_members(team_name,user_id) VALUES(?,?)", (tn, uid))
    conn.commit()
    regenerate_teams_json()
    await message.reply(f"{mention} added to '{tn}'.")

@dp.message_handler(commands=['my_team'])
async def cmd_my_team(message: types.Message):
    uid = message.from_user.id
    rows = cursor.execute("SELECT team_name FROM team_members WHERE user_id=?", (uid,)).fetchall()
    if not rows:
        return await message.reply("You are not in any team.")
    await message.reply("Your teams: " + ", ".join(r[0] for r in rows))

@dp.message_handler(commands=['team_projects'])
async def cmd_team_projects(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Usage: /team_projects <team>")
    tn = parts[1].strip()
    uid = message.from_user.id
    if not cursor.execute("SELECT 1 FROM team_members WHERE team_name=? AND user_id=?", (tn, uid)).fetchone():
        return await message.reply("You are not a member of this team.")
    rows = cursor.execute(
        "SELECT token,name FROM games WHERE owner_id IN (SELECT user_id FROM team_members WHERE team_name=?)", (tn,)
    ).fetchall()
    if not rows:
        return await message.reply("No projects in this team.")
    await message.reply("Projects: " + ", ".join(f"{t}:{n}" for t,n in rows))

@dp.message_handler(commands=['delete_project'])
async def cmd_delete_project(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Usage: /delete_project <token>")
    tk = parts[1].strip()
    row = cursor.execute("SELECT photo_path,zip_path,owner_id FROM games WHERE token=?", (tk,)).fetchone()
    if not row:
        return await message.reply("Project not found.")
    if message.from_user.id != row[2]:
        return await message.reply("Only the owner can delete this project.")
    try:
        os.remove(row[0]); os.remove(row[1]); os.rmdir(os.path.dirname(row[0]))
    except:
        pass
    cursor.execute("DELETE FROM games WHERE token=?", (tk,))
    conn.commit()
    regenerate_projects_json()
    await message.reply(f"Project {tk} deleted.")

# Jam request handlers
@dp.message_handler(commands=['create_jam'])
async def jam1(message: types.Message):
    await message.reply("Enter jam title:")
    await JamStates.title.set()

@dp.message_handler(state=JamStates.title, content_types=ContentType.TEXT)
async def jam2(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.reply("Enter conditions:")
    await JamStates.conditions.set()

@dp.message_handler(state=JamStates.conditions, content_types=ContentType.TEXT)
async def jam3(message: types.Message, state: FSMContext):
    await state.update_data(conditions=message.text.strip())
    await message.reply("Enter reward (optional):")
    await JamStates.reward.set()

@dp.message_handler(state=JamStates.reward, content_types=ContentType.TEXT)
async def jam4(message: types.Message, state: FSMContext):
    await state.update_data(reward=message.text.strip())
    await message.reply("Enter max participants:")
    await JamStates.participants.set()

@dp.message_handler(state=JamStates.participants, content_types=ContentType.TEXT)
async def jam5(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.reply("Please enter a number.")
    await state.update_data(participants=int(message.text))
    await message.reply("Enter contact info:")
    await JamStates.contact.set()

@dp.message_handler(state=JamStates.contact, content_types=ContentType.TEXT)
async def jam6(message: types.Message, state: FSMContext):
    data = await state.get_data()
    contact = message.text.strip()
    cursor.execute(
        "INSERT INTO jam_requests(title,conditions,reward,participants,contact,requester_id) VALUES(?,?,?,?,?,?)",
        (data['title'], data['conditions'], data['reward'], data['participants'], contact, message.from_user.id)
    )
    req_id = cursor.lastrowid
    conn.commit()
    # Notify admin
    text = (
        f"Jam Request #{req_id}:\n"
        f"Title: {data['title']}\n"
        f"Conditions: {data['conditions']}\n"
        f"Reward: {data['reward']}\n"
        f"Participants: {data['participants']}\n"
        f"Contact: {contact}\n"
        f"From: @{message.from_user.username or message.from_user.id}"
    )
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton('✅ Approve', callback_data=f"approve_jam:{req_id}"),
        InlineKeyboardButton('❌ Reject', callback_data=f"reject_jam:{req_id}")
    )
    await bot.send_message(ADMIN_ID, text, reply_markup=kb)
    await message.reply("Your request has been sent for approval.")
    await state.finish()

# Callbacks for jam approval/rejection
@dp.callback_query_handler(lambda c: c.data.startswith('approve_jam:'))
async def approve_jam(call: types.CallbackQuery):
    _, rid = call.data.split(':',1)
    row = cursor.execute(
        "SELECT title,conditions,reward,participants,contact FROM jam_requests WHERE id=?", (rid,)
    ).fetchone()
    if row:
        cursor.execute(
            "INSERT INTO jams(title,conditions,reward,participants,contact) VALUES(?,?,?,?,?)", row
        )
        cursor.execute("DELETE FROM jam_requests WHERE id=?", (rid,))
        conn.commit()
        regenerate_jams_json()
        await call.message.edit_text(call.message.text + "\n\n✅ Approved and published.")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('reject_jam:'))
async def reject_jam(call: types.CallbackQuery):
    _, rid = call.data.split(':',1)
    cursor.execute("DELETE FROM jam_requests WHERE id=?", (rid,))
    conn.commit()
    await call.message.edit_text(call.message.text + "\n\n❌ Rejected.")
    await call.answer()

# Web server to serve JSON and static files
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