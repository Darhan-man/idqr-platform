from fastapi import FastAPI, Form, Request, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
import qrcode
import os
import uuid
from datetime import datetime, timedelta
import aiosqlite
from PIL import Image, ImageDraw, ImageFont, ImageColor
import logging
import textwrap
import json
import secrets
from passlib.context import CryptContext
import ipaddress
from typing import Optional

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Секретный ключ для сессий (в продакшене должен быть в переменных окружения)
SECRET_KEY = os.environ.get("SECRET_KEY", "your-secret-key-change-in-production")

# Создаем middleware для сессий
middleware = [
    Middleware(SessionMiddleware, secret_key=SECRET_KEY)
]

app = FastAPI(middleware=middleware)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Константы ---
QR_FOLDER = "static/qr"
LOGOS_FOLDER = "static/logos"
DB_PATH = "qr_data.db"
ADMIN_CODE = "admin1990"
BASE_URL = "https://idqr-platform.onrender.com"

# Настройка безопасности - используем argon2 вместо bcrypt чтобы избежать ограничения длины
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Создаем папки если они не существуют
os.makedirs(QR_FOLDER, exist_ok=True)
os.makedirs(LOGOS_FOLDER, exist_ok=True)
os.makedirs("static/fonts", exist_ok=True)

# --- ИНИЦИАЛИЗАЦИЯ БД ---
@app.on_event("startup")
async def startup():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Таблица QR-кодов
            await db.execute("""
                CREATE TABLE IF NOT EXISTS qr_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    data TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    scan_count INTEGER DEFAULT 0,
                    last_scan TEXT,
                    colors TEXT DEFAULT '{"qr_color": "#000000", "bg_color": "#FFFFFF", "text_color": "#000000"}',
                    user_id INTEGER
                )
            """)
            
            # Таблица пользователей с расширенными полями
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login TEXT,
                    is_blocked BOOLEAN NOT NULL DEFAULT 0,
                    frozen_until TEXT,
                    block_count INTEGER DEFAULT 0,
                    theme TEXT NOT NULL DEFAULT 'light',
                    logo_url TEXT,
                    ip_address TEXT
                )
            """)
            
            # Таблица заблокированных IP
            await db.execute("""
                CREATE TABLE IF NOT EXISTS blocked_ips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip_address TEXT UNIQUE NOT NULL,
                    reason TEXT,
                    blocked_until TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            
            # Таблица системных настроек
            await db.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    setting_key TEXT UNIQUE NOT NULL,
                    setting_value TEXT NOT NULL,
                    description TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Таблица логов действий
            await db.execute("""
                CREATE TABLE IF NOT EXISTS action_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    ip_address TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            
            # Создаем администратора по умолчанию
            admin_password = "admin123"
            await db.execute("""
                INSERT OR IGNORE INTO users (username, password_hash, role, created_at) 
                VALUES (?, ?, ?, ?)
            """, ("admin", pwd_context.hash(admin_password), "admin", datetime.now().isoformat()))
            
            # Создаем базовые системные настройки
            await db.execute("""
                INSERT OR IGNORE INTO system_settings (setting_key, setting_value, description, updated_at) 
                VALUES (?, ?, ?, ?)
            """, ("site_name", "IDQR Platform", "Название сайта", datetime.now().isoformat()))
            
            await db.execute("""
                INSERT OR IGNORE INTO system_settings (setting_key, setting_value, description, updated_at) 
                VALUES (?, ?, ?, ?)
            """, ("max_qr_per_user", "50", "Максимум QR-кодов на пользователя", datetime.now().isoformat()))
            
            await db.execute("""
                INSERT OR IGNORE INTO system_settings (setting_key, setting_value, description, updated_at) 
                VALUES (?, ?, ?, ?)
            """, ("registration_enabled", "true", "Разрешена ли регистрация новых пользователей", datetime.now().isoformat()))
            
            await db.commit()
            logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка при инициализации БД: {e}")

# --- Функции аутентификации и утилиты ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    # Обрезаем пароль до 72 символов для совместимости
    if len(password) > 72:
        password = password[:72]
    return pwd_context.hash(password)

async def check_ip_blocked(ip_address: str) -> bool:
    """Проверяет заблокирован ли IP"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT blocked_until FROM blocked_ips WHERE ip_address = ?", 
                (ip_address,)
            )
            result = await cursor.fetchone()
            
            if result:
                blocked_until = result[0]
                if blocked_until:
                    # Временная блокировка
                    if datetime.now() < datetime.fromisoformat(blocked_until):
                        return True
                    else:
                        # Время блокировки истекло - удаляем запись
                        await db.execute("DELETE FROM blocked_ips WHERE ip_address = ?", (ip_address,))
                        await db.commit()
                        return False
                else:
                    # Перманентная блокировка
                    return True
    except Exception as e:
        logger.error(f"Ошибка при проверке IP: {e}")
    
    return False

async def log_action(user_id: int, action_type: str, description: str, ip_address: str = None):
    """Логирование действий пользователей"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO action_logs (user_id, action_type, description, ip_address, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, action_type, description, ip_address, datetime.now().isoformat())
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при логировании действия: {e}")

async def authenticate_user(username: str, password: str, ip_address: str):
    try:
        # Сначала проверяем блокировку IP
        if await check_ip_blocked(ip_address):
            return {"error": "ip_blocked", "message": "Ваш IP-адрес заблокирован"}
        
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT * FROM users WHERE username = ?", 
                (username,)
            )
            user = await cursor.fetchone()
            
        if user:
            # Проверка блокировки аккаунта
            if user[7]:  # is_blocked
                return {"error": "blocked", "message": "Ваш аккаунт заблокирован за нарушения"}
            
            # Проверка заморозки
            if user[8]:  # frozen_until
                freeze_until = datetime.fromisoformat(user[8])
                if datetime.now() < freeze_until:
                    return {
                        "error": "frozen", 
                        "message": f"Ваш аккаунт заморожен за нарушения. Разблокировка через: {freeze_until.strftime('%d.%m.%Y %H:%M')}"
                    }
                else:
                    # Автоматическая разморозка при истечении времени
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "UPDATE users SET frozen_until = NULL WHERE id = ?",
                            (user[0],)
                        )
                        await db.commit()
            
            # Проверка активности
            if not user[4]:  # is_active
                return {"error": "inactive", "message": "Ваш аккаунт деактивирован"}
            
            # Проверка пароля
            if verify_password(password, user[2]):
                # Обновляем IP адрес
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE users SET ip_address = ?, last_login = ? WHERE id = ?",
                        (ip_address, datetime.now().isoformat(), user[0])
                    )
                    await db.commit()
                
                # Логируем вход
                await log_action(user[0], "login", "Успешный вход в систему", ip_address)
                return user
        
        # Логируем неудачную попытку входа
        await log_action(None, "failed_login", f"Неудачная попытка входа для пользователя {username}", ip_address)
        return None
    except Exception as e:
        logger.error(f"Ошибка аутентификации: {e}")
        return None

async def get_current_user(request: Request):
    try:
        user_id = request.session.get("user_id")
        client_ip = request.client.host
        
        # Проверяем блокировку IP
        if await check_ip_blocked(client_ip):
            return {"error": "ip_blocked", "message": "Ваш IP-адрес заблокирован"}
        
        if user_id:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                user = await cursor.fetchone()
            
            if user:
                # Проверка статуса пользователя
                if user[7]:  # is_blocked
                    return {"error": "blocked", "message": "Ваш аккаунт заблокирован за нарушения"}
                
                if user[8]:  # frozen_until
                    freeze_until = datetime.fromisoformat(user[8])
                    if datetime.now() < freeze_until:
                        return {
                            "error": "frozen", 
                            "message": f"Ваш аккаунт заморожен за нарушения. Разблокировка через: {freeze_until.strftime('%d.%m.%Y %H:%M')}"
                        }
                    else:
                        # Автоматическая разморозка
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute(
                                "UPDATE users SET frozen_until = NULL WHERE id = ?",
                                (user[0],)
                            )
                            await db.commit()
                
                if not user[4]:  # is_active
                    return {"error": "inactive", "message": "Ваш аккаунт деактивирован"}
                
                return user
        
        return None
    except Exception as e:
        logger.error(f"Ошибка в get_current_user: {e}")
        return None

def get_client_ip(request: Request):
    return request.client.host

# --- РЕГИСТРАЦИЯ ---
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    # Проверяем разрешена ли регистрация
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'registration_enabled'")
            result = await cursor.fetchone()
            registration_enabled = result[0] == 'true' if result else True
            
        if not registration_enabled:
            return templates.TemplateResponse("register.html", {
                "request": request, 
                "error": "Регистрация новых пользователей временно отключена"
            })
    except Exception as e:
        logger.error(f"Ошибка при проверке настроек регистрации: {e}")
    
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def register(
    request: Request, 
    username: str = Form(...), 
    password: str = Form(...)
):
    client_ip = get_client_ip(request)
    
    # Проверяем блокировку IP
    if await check_ip_blocked(client_ip):
        return templates.TemplateResponse("register.html", {
            "request": request, 
            "error": "Ваш IP-адрес заблокирован. Регистрация невозможна."
        })
    
    # Проверяем разрешена ли регистрация
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'registration_enabled'")
            result = await cursor.fetchone()
            registration_enabled = result[0] == 'true' if result else True
            
        if not registration_enabled:
            return templates.TemplateResponse("register.html", {
                "request": request, 
                "error": "Регистрация новых пользователей временно отключена"
            })
    except Exception as e:
        logger.error(f"Ошибка при проверке настроек регистрации: {e}")
    
    try:
        # Проверяем существование пользователя
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id FROM users WHERE username = ?", (username,))
            existing_user = await cursor.fetchone()
            
            if existing_user:
                return templates.TemplateResponse("register.html", {
                    "request": request, 
                    "error": "Пользователь с таким именем уже существует"
                })
            
            # Создаем пользователя с ролью 'user' по умолчанию
            password_hash = get_password_hash(password)
            created_at = datetime.now().isoformat()
            
            await db.execute(
                "INSERT INTO users (username, password_hash, role, created_at, ip_address) VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, "user", created_at, client_ip)
            )
            await db.commit()
            
            # Получаем ID нового пользователя
            cursor = await db.execute("SELECT id FROM users WHERE username = ?", (username,))
            new_user = await cursor.fetchone()
            
            # Логируем регистрацию
            await log_action(new_user[0], "registration", "Новый пользователь зарегистрирован", client_ip)
            
        return RedirectResponse(url="/user/login", status_code=303)
        
    except Exception as e:
        logger.error(f"Ошибка при регистрации: {e}")
        return templates.TemplateResponse("register.html", {
            "request": request, 
            "error": "Ошибка при регистрации"
        })

# --- ГЛАВНАЯ СТРАНИЦА ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    client_ip = get_client_ip(request)
    
    # Проверяем блокировку IP
    if await check_ip_blocked(client_ip):
        return templates.TemplateResponse("ip_blocked.html", {
            "request": request,
            "message": "Ваш IP-адрес заблокирован"
        })
    
    # Проверяем, авторизован ли пользователь
    user = await get_current_user(request)
    if user and not isinstance(user, dict):
        if user[3] == "admin":
            return RedirectResponse(url="/dashboard/qr", status_code=303)
        else:
            return RedirectResponse(url="/user/dashboard", status_code=303)
    elif isinstance(user, dict):
        # Пользователь заблокирован или заморожен
        if user["error"] == "frozen":
            return templates.TemplateResponse("account_frozen.html", {
                "request": request,
                "message": user["message"]
            })
        else:
            return templates.TemplateResponse("user_blocked.html", {
                "request": request,
                "message": user["message"]
            })
    
    return templates.TemplateResponse("index.html", {"request": request})

# --- ВХОД АДМИНИСТРАТОРА ---
@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == ADMIN_CODE:
        # Находим пользователя admin
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM users WHERE username = 'admin'")
            admin_user = await cursor.fetchone()
        
        if admin_user:
            request.session["user_id"] = admin_user[0]
            request.session["user_role"] = admin_user[3]
            
            # Логируем вход администратора
            await log_action(admin_user[0], "admin_login", "Вход администратора через код", get_client_ip(request))
            
            return RedirectResponse(url="/dashboard/qr", status_code=303)
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "error": "Неверный код",
        "show_user_hint": True
    })

# --- ВХОД ПОЛЬЗОВАТЕЛЯ ---
@app.get("/user/login", response_class=HTMLResponse)
async def user_login_page(request: Request):
    return templates.TemplateResponse("user_login.html", {"request": request})

@app.post("/user/login", response_class=HTMLResponse)
async def user_login(request: Request, username: str = Form(...), password: str = Form(...)):
    client_ip = get_client_ip(request)
    result = await authenticate_user(username, password, client_ip)
    
    if isinstance(result, dict) and "error" in result:
        if result["error"] == "frozen":
            return templates.TemplateResponse("account_frozen.html", {
                "request": request,
                "message": result["message"]
            })
        else:
            return templates.TemplateResponse("user_login.html", {
                "request": request, 
                "error": result["message"]
            })
    elif result:
        request.session["user_id"] = result[0]
        request.session["user_role"] = result[3]
        return RedirectResponse(url="/user/dashboard", status_code=303)
    
    return templates.TemplateResponse("user_login.html", {
        "request": request, 
        "error": "Неверный логин или пароль"
    })

# --- ВЫХОД ---
@app.get("/logout")
async def logout(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        await log_action(user_id, "logout", "Выход из системы", get_client_ip(request))
    
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)

# --- ПАНЕЛЬ АДМИНИСТРАТОРА ---
async def check_admin(request: Request):
    user = await get_current_user(request)
    if isinstance(user, dict):
        return user
    if not user or user[3] != "admin":
        return RedirectResponse(url="/", status_code=303)
    return user

# --- ПАНЕЛЬ ПОЛЬЗОВАТЕЛЯ/ИП ---
async def check_user_access(request: Request):
    user = await get_current_user(request)
    if isinstance(user, dict):
        return user
    if not user:
        return RedirectResponse(url="/user/login", status_code=303)
    return user

async def check_ip_access(request: Request):
    """Проверка доступа для ИП (может создавать QR)"""
    user = await get_current_user(request)
    if isinstance(user, dict):
        return user
    if not user:
        return RedirectResponse(url="/user/login", status_code=303)
    if user[3] not in ["ip", "admin"]:
        return RedirectResponse(url="/user/dashboard", status_code=303)
    return user

# --- QR-КОДЫ (для админа и ИП) ---
@app.get("/dashboard/qr", response_class=HTMLResponse)
async def dashboard_qr(request: Request):
    user = await check_ip_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if user[3] == "admin":
                cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
            else:
                cursor = await db.execute("SELECT * FROM qr_codes WHERE user_id = ? ORDER BY id DESC", (user[0],))
            qr_list = await cursor.fetchall()
        
        return templates.TemplateResponse("qr.html", {
            "request": request,
            "qr_list": qr_list,
            "qr_url": None,
            "qr_title": None,
            "active": "qr",
            "user": user
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке QR-кодов: {e}")
        return templates.TemplateResponse("qr.html", {
            "request": request,
            "qr_list": [],
            "qr_url": None,
            "qr_title": None,
            "active": "qr",
            "user": user,
            "error": "Ошибка при загрузке данных"
        })

# --- Генерация QR (для админа и ИП) ---
@app.post("/generate_qr")
async def generate_qr(
    request: Request, 
    qrdata: str = Form(...), 
    title: str = Form(...),
    qr_color: str = Form("#000000"),
    text_color: str = Form("#000000")
):
    user = await check_ip_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        # Проверяем лимит QR-кодов для пользователя
        if user[3] != "admin":
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM qr_codes WHERE user_id = ?", (user[0],))
                qr_count = await cursor.fetchone()
                
                cursor = await db.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'max_qr_per_user'")
                max_qr_result = await cursor.fetchone()
                max_qr = int(max_qr_result[0]) if max_qr_result else 50
                
                if qr_count[0] >= max_qr:
                    return templates.TemplateResponse("qr.html", {
                        "request": request,
                        "error": f"Превышен лимит QR-кодов. Максимум: {max_qr}",
                        "user": user
                    })
        
        # Генерируем уникальное имя файла
        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(QR_FOLDER, filename)

        # Сохраняем цвета в формате JSON
        colors_json = json.dumps({
            "qr_color": qr_color,
            "bg_color": "#FFFFFF",
            "text_color": text_color
        })

        # Создаем запись в БД
        async with aiosqlite.connect(DB_PATH) as db:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = await db.execute(
                "INSERT INTO qr_codes (title, data, filename, created_at, colors, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                (title, qrdata, filename, now, colors_json, user[0])
            )
            await db.commit()
            qr_id = cursor.lastrowid

        # Генерируем QR-код
        scan_url = f"{BASE_URL}/scan/{qr_id}"
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(scan_url)
        qr.make(fit=True)
        
        qr_img = qr.make_image(fill_color=qr_color, back_color="white").convert("RGB")
        
        # Добавляем текст
        try:
            font = ImageFont.truetype("static/fonts/RobotoSlab-Bold.ttf", 28)
        except IOError:
            font = ImageFont.load_default()
        
        max_chars_per_line = 20
        wrapped_text = textwrap.fill(title, width=max_chars_per_line)
        lines = wrapped_text.split('\n')
        
        line_height = 30
        text_height = len(lines) * line_height + 20
        
        new_img = Image.new("RGB", (qr_img.width, qr_img.height + text_height), "white")
        new_img.paste(qr_img, (0, text_height))
        
        draw = ImageDraw.Draw(new_img)
        y = 10
        for line in lines:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_x = (new_img.width - text_width) // 2
            draw.text((text_x, y), line, font=font, fill=text_color)
            y += line_height
        
        new_img.save(filepath)

        # Логируем создание QR-кода
        await log_action(user[0], "qr_create", f"Создан QR-код: {title}")
        
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    
    except Exception as e:
        logger.error(f"Ошибка при генерации QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- Просмотр QR кода ---
@app.get("/dashboard/qr/view/{qr_id}", response_class=HTMLResponse)
async def view_qr(request: Request, qr_id: int):
    user = await check_ip_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,))
            qr_code = await cursor.fetchone()
            
            if not qr_code:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
                
            # Проверяем права доступа
            if user[3] != "admin" and qr_code[8] != user[0]:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
                
            qr_url = f"/static/qr/{qr_code[3]}"
            
            return templates.TemplateResponse("view_qr.html", {
                "request": request,
                "qr_code": qr_code,
                "qr_url": qr_url,
                "active": "qr",
                "user": user
            })
    except Exception as e:
        logger.error(f"Ошибка при просмотре QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- Редактирование QR кода ---
@app.get("/dashboard/qr/edit/{qr_id}", response_class=HTMLResponse)
async def edit_qr_page(request: Request, qr_id: int):
    user = await check_ip_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,))
            qr_code = await cursor.fetchone()
            
            if not qr_code:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
                
            # Проверяем права доступа
            if user[3] != "admin" and qr_code[8] != user[0]:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
            
            # Получаем цвета
            colors = json.loads(qr_code[7]) if qr_code[7] else {"qr_color": "#000000", "bg_color": "#FFFFFF", "text_color": "#000000"}
            
            return templates.TemplateResponse("edit_qr.html", {
                "request": request,
                "qr_code": qr_code,
                "colors": colors,
                "active": "qr",
                "user": user
            })
    except Exception as e:
        logger.error(f"Ошибка при загрузке формы редактирования QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- Обновление QR кода ---
@app.post("/dashboard/qr/update/{qr_id}")
async def update_qr(
    request: Request, 
    qr_id: int,
    title: str = Form(...),
    qrdata: str = Form(...),
    qr_color: str = Form("#000000"),
    text_color: str = Form("#000000")
):
    user = await check_ip_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        # Получаем старый QR-код
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,))
            old_qr = await cursor.fetchone()
            
            if not old_qr:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
                
            # Проверяем права доступа
            if user[3] != "admin" and old_qr[8] != user[0]:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
            
            # Обновляем данные в БД
            colors_json = json.dumps({
                "qr_color": qr_color,
                "bg_color": "#FFFFFF",
                "text_color": text_color
            })
            
            await db.execute(
                "UPDATE qr_codes SET title = ?, data = ?, colors = ? WHERE id = ?",
                (title, qrdata, colors_json, qr_id)
            )
            await db.commit()
        
        # Перегенерируем QR-код
        filename = old_qr[3]
        filepath = os.path.join(QR_FOLDER, filename)
        
        scan_url = f"{BASE_URL}/scan/{qr_id}"
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(scan_url)
        qr.make(fit=True)
        
        qr_img = qr.make_image(fill_color=qr_color, back_color="white").convert("RGB")
        
        # Добавляем текст
        try:
            font = ImageFont.truetype("static/fonts/RobotoSlab-Bold.ttf", 28)
        except IOError:
            font = ImageFont.load_default()
        
        max_chars_per_line = 20
        wrapped_text = textwrap.fill(title, width=max_chars_per_line)
        lines = wrapped_text.split('\n')
        
        line_height = 30
        text_height = len(lines) * line_height + 20
        
        new_img = Image.new("RGB", (qr_img.width, qr_img.height + text_height), "white")
        new_img.paste(qr_img, (0, text_height))
        
        draw = ImageDraw.Draw(new_img)
        y = 10
        for line in lines:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_x = (new_img.width - text_width) // 2
            draw.text((text_x, y), line, font=font, fill=text_color)
            y += line_height
        
        new_img.save(filepath)

        # Логируем обновление QR-кода
        await log_action(user[0], "qr_update", f"Обновлен QR-код: {title}")
        
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    
    except Exception as e:
        logger.error(f"Ошибка при обновлении QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- СКАНИРОВАНИЕ QR ---
@app.get("/scan/{qr_id}")
async def scan_qr(qr_id: int):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT data, scan_count FROM qr_codes WHERE id = ?", (qr_id,))
            row = await cursor.fetchone()
            if row:
                data, scan_count = row
                await db.execute(
                    "UPDATE qr_codes SET scan_count = ?, last_scan = ? WHERE id = ?",
                    (scan_count + 1, datetime.now().isoformat(), qr_id)
                )
                await db.commit()
                return RedirectResponse(data)
        return RedirectResponse("/", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при сканировании QR-кода: {e}")
        return RedirectResponse("/", status_code=303)

# --- УДАЛЕНИЕ QR ---
@app.get("/delete_qr/{qr_id}")
async def delete_qr(request: Request, qr_id: int):
    user = await check_ip_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем владельца QR-кода
            cursor = await db.execute("SELECT user_id, title FROM qr_codes WHERE id = ?", (qr_id,))
            qr_owner = await cursor.fetchone()
            
            if qr_owner and (user[3] == "admin" or qr_owner[0] == user[0]):
                cursor = await db.execute("SELECT filename FROM qr_codes WHERE id = ?", (qr_id,))
                row = await cursor.fetchone()
                if row:
                    filename = row[0]
                    path = os.path.join(QR_FOLDER, filename)
                    if os.path.exists(path):
                        os.remove(path)
                    await db.execute("DELETE FROM qr_codes WHERE id = ?", (qr_id,))
                    await db.commit()
                    
                    # Логируем удаление QR-кода
                    await log_action(user[0], "qr_delete", f"Удален QR-код: {qr_owner[1]}")
        
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при удалении QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (админ) ---
@app.get("/dashboard/users", response_class=HTMLResponse)
async def users_management(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, username, role, is_active, created_at, last_login, 
                       is_blocked, frozen_until, block_count, ip_address 
                FROM users ORDER BY id DESC
            """)
            users_list = await cursor.fetchall()
        
        return templates.TemplateResponse("users.html", {
            "request": request,
            "active": "users",
            "users_list": users_list,
            "user": user
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке пользователей: {e}")
        return templates.TemplateResponse("users.html", {
            "request": request,
            "active": "users",
            "users_list": [],
            "user": user,
            "error": "Ошибка при загрузке данных"
        })

# --- ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ (админ) ---
@app.post("/dashboard/users/add")
async def add_user(
    request: Request, 
    username: str = Form(...), 
    password: str = Form(...),
    role: str = Form("user")
):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        password_hash = get_password_hash(password)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, datetime.now().isoformat())
            )
            await db.commit()
            
            # Логируем добавление пользователя
            await log_action(user[0], "user_add", f"Добавлен пользователь: {username} с ролью {role}")
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- ИЗМЕНЕНИЕ РОЛИ ПОЛЬЗОВАТЕЛЯ (админ) ---
@app.post("/dashboard/users/change_role/{user_id}")
async def change_user_role(
    request: Request, 
    user_id: int,
    new_role: str = Form(...)
):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Нельзя менять роль администратора
            cursor = await db.execute("SELECT role, username FROM users WHERE id = ?", (user_id,))
            current_user = await cursor.fetchone()
            
            if current_user and current_user[0] != "admin":
                await db.execute(
                    "UPDATE users SET role = ? WHERE id = ?",
                    (new_role, user_id)
                )
                await db.commit()
                
                # Логируем изменение роли
                await log_action(user[0], "user_role_change", f"Изменена роль пользователя {current_user[1]} на {new_role}")
                
    except Exception as e:
        logger.error(f"Ошибка при изменении роли пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- БЛОКИРОВКА/ЗАМОРОЗКА ПОЛЬЗОВАТЕЛЯ С ВЫБОРОМ ВРЕМЕНИ ---
@app.post("/dashboard/users/block/{user_id}")
async def block_user(
    request: Request, 
    user_id: int,
    block_type: str = Form(...),
    block_duration: str = Form("1"),
    block_unit: str = Form("hours")
):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT username FROM users WHERE id = ?", (user_id,))
            target_user = await cursor.fetchone()
            
            if block_type == "permanent":
                # Перманентная блокировка
                await db.execute(
                    "UPDATE users SET is_blocked = 1, frozen_until = NULL, block_count = block_count + 1 WHERE id = ? AND role != 'admin'",
                    (user_id,)
                )
                # Логируем блокировку
                await log_action(user[0], "user_block", f"Заблокирован пользователь: {target_user[0]}")
            else:
                # Временная блокировка (заморозка)
                duration = int(block_duration)
                if block_unit == "hours":
                    freeze_until = datetime.now() + timedelta(hours=duration)
                elif block_unit == "days":
                    freeze_until = datetime.now() + timedelta(days=duration)
                else:  # weeks
                    freeze_until = datetime.now() + timedelta(weeks=duration)
                
                await db.execute(
                    "UPDATE users SET is_blocked = 0, frozen_until = ?, block_count = block_count + 1 WHERE id = ? AND role != 'admin'",
                    (freeze_until.isoformat(), user_id)
                )
                # Логируем заморозку
                await log_action(user[0], "user_freeze", f"Заморожен пользователь: {target_user[0]} до {freeze_until.strftime('%d.%m.%Y %H:%M')}")
            
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при блокировке пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- РАЗБЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ ---
@app.get("/dashboard/users/unblock/{user_id}")
async def unblock_user(request: Request, user_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT username FROM users WHERE id = ?", (user_id,))
            target_user = await cursor.fetchone()
            
            await db.execute(
                "UPDATE users SET is_blocked = 0, frozen_until = NULL WHERE id = ?",
                (user_id,)
            )
            await db.commit()
            
            # Логируем разблокировку
            await log_action(user[0], "user_unblock", f"Разблокирован пользователь: {target_user[0]}")
            
    except Exception as e:
        logger.error(f"Ошибка при разблокировке пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ---
@app.get("/dashboard/users/delete/{user_id}")
async def delete_user(request: Request, user_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Нельзя удалить администратора
            cursor = await db.execute("SELECT role, username FROM users WHERE id = ?", (user_id,))
            user_role = await cursor.fetchone()
            
            if user_role and user_role[0] != "admin":
                await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
                await db.commit()
                
                # Логируем удаление пользователя
                await log_action(user[0], "user_delete", f"Удален пользователь: {user_role[1]}")
                
    except Exception as e:
        logger.error(f"Ошибка при удалении пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- БЛОКИРОВКА IP ---
@app.post("/dashboard/ip/block")
async def block_ip(
    request: Request,
    ip_address: str = Form(...),
    reason: str = Form(""),
    block_type: str = Form(...),
    block_duration: str = Form("1"),
    block_unit: str = Form("hours")
):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        # Валидация IP-адреса
        ipaddress.ip_address(ip_address)
        
        async with aiosqlite.connect(DB_PATH) as db:
            if block_type == "permanent":
                await db.execute(
                    "INSERT OR REPLACE INTO blocked_ips (ip_address, reason, blocked_until, created_at) VALUES (?, ?, NULL, ?)",
                    (ip_address, reason, datetime.now().isoformat())
                )
                # Логируем блокировку IP
                await log_action(user[0], "ip_block", f"Заблокирован IP: {ip_address} (постоянно)")
            else:
                duration = int(block_duration)
                if block_unit == "hours":
                    blocked_until = datetime.now() + timedelta(hours=duration)
                elif block_unit == "days":
                    blocked_until = datetime.now() + timedelta(days=duration)
                else:  # weeks
                    blocked_until = datetime.now() + timedelta(weeks=duration)
                
                await db.execute(
                    "INSERT OR REPLACE INTO blocked_ips (ip_address, reason, blocked_until, created_at) VALUES (?, ?, ?, ?)",
                    (ip_address, reason, blocked_until.isoformat(), datetime.now().isoformat())
                )
                # Логируем блокировку IP
                await log_action(user[0], "ip_block", f"Заблокирован IP: {ip_address} до {blocked_until.strftime('%d.%m.%Y %H:%M')}")
            
            await db.commit()
    except ValueError:
        return RedirectResponse(url="/dashboard/ip?error=invalid_ip", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при блокировке IP: {e}")
    
    return RedirectResponse(url="/dashboard/ip", status_code=303)

# --- РАЗБЛОКИРОВКА IP ---
@app.get("/dashboard/ip/unblock/{ip_address}")
async def unblock_ip(request: Request, ip_address: str):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM blocked_ips WHERE ip_address = ?", (ip_address,))
            await db.commit()
            
            # Логируем разблокировку IP
            await log_action(user[0], "ip_unblock", f"Разблокирован IP: {ip_address}")
            
    except Exception as e:
        logger.error(f"Ошибка при разблокировке IP: {e}")
    
    return RedirectResponse(url="/dashboard/ip", status_code=303)

# --- УПРАВЛЕНИЕ IP (админ) ---
@app.get("/dashboard/ip", response_class=HTMLResponse)
async def ip_management(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM blocked_ips ORDER BY created_at DESC")
            ip_list = await cursor.fetchall()
        
        return templates.TemplateResponse("ip_management.html", {
            "request": request,
            "active": "ip",
            "ip_list": ip_list,
            "user": user
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке IP: {e}")
        return templates.TemplateResponse("ip_management.html", {
            "request": request,
            "active": "ip",
            "ip_list": [],
            "user": user,
            "error": "Ошибка при загрузке данных"
        })

# --- СИСТЕМНЫЕ НАСТРОЙКИ (админ) ---
@app.get("/dashboard/system", response_class=HTMLResponse)
async def system_settings(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM system_settings ORDER BY setting_key")
            settings_list = await cursor.fetchall()
        
        return templates.TemplateResponse("system_settings.html", {
            "request": request,
            "active": "system",
            "settings_list": settings_list,
            "user": user
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке системных настроек: {e}")
        return templates.TemplateResponse("system_settings.html", {
            "request": request,
            "active": "system",
            "settings_list": [],
            "user": user,
            "error": "Ошибка при загрузке данных"
        })

# --- ОБНОВЛЕНИЕ СИСТЕМНЫХ НАСТРОЕК ---
@app.post("/dashboard/system/update")
async def update_system_settings(
    request: Request,
    setting_key: str = Form(...),
    setting_value: str = Form(...)
):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE system_settings SET setting_value = ?, updated_at = ? WHERE setting_key = ?",
                (setting_value, datetime.now().isoformat(), setting_key)
            )
            await db.commit()
            
            # Логируем изменение настроек
            await log_action(user[0], "system_settings_update", f"Обновлена настройка: {setting_key} = {setting_value}")
            
    except Exception as e:
        logger.error(f"Ошибка при обновлении системных настроек: {e}")
    
    return RedirectResponse(url="/dashboard/system", status_code=303)

# --- ЛОГИ СИСТЕМЫ (админ) ---
@app.get("/dashboard/logs", response_class=HTMLResponse)
async def system_logs(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT al.*, u.username 
                FROM action_logs al 
                LEFT JOIN users u ON al.user_id = u.id 
                ORDER BY al.created_at DESC 
                LIMIT 100
            """)
            logs_list = await cursor.fetchall()
        
        return templates.TemplateResponse("system_logs.html", {
            "request": request,
            "active": "logs",
            "logs_list": logs_list,
            "user": user
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке логов: {e}")
        return templates.TemplateResponse("system_logs.html", {
            "request": request,
            "active": "logs",
            "logs_list": [],
            "user": user,
            "error": "Ошибка при загрузке данных"
        })

# --- СТАТИСТИКА СИСТЕМЫ (админ) ---
@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Общая статистика
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            total_users = await cursor.fetchone()
            
            cursor = await db.execute("SELECT COUNT(*) FROM qr_codes")
            total_qr = await cursor.fetchone()
            
            cursor = await db.execute("SELECT SUM(scan_count) FROM qr_codes")
            total_scans = await cursor.fetchone()
            
            cursor = await db.execute("SELECT COUNT(*) FROM blocked_ips")
            total_blocked_ips = await cursor.fetchone()
            
            # Статистика по ролям
            cursor = await db.execute("SELECT role, COUNT(*) FROM users GROUP BY role")
            roles_stats = await cursor.fetchall()
            
            # Последние QR-коды
            cursor = await db.execute("""
                SELECT qr.id, qr.title, qr.scan_count, qr.created_at, u.username 
                FROM qr_codes qr 
                LEFT JOIN users u ON qr.user_id = u.id 
                ORDER BY qr.created_at DESC 
                LIMIT 10
            """)
            recent_qr = await cursor.fetchall()
            
            # Статистика по сканированиям
            cursor = await db.execute("""
                SELECT DATE(created_at) as date, COUNT(*) as count 
                FROM qr_codes 
                WHERE created_at >= date('now', '-30 days') 
                GROUP BY DATE(created_at) 
                ORDER BY date DESC
            """)
            scans_stats = await cursor.fetchall()
        
        return templates.TemplateResponse("stats.html", {
            "request": request,
            "active": "stats",
            "total_users": total_users[0],
            "total_qr": total_qr[0],
            "total_scans": total_scans[0] or 0,
            "total_blocked_ips": total_blocked_ips[0],
            "roles_stats": roles_stats,
            "recent_qr": recent_qr,
            "scans_stats": scans_stats,
            "user": user
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке статистики: {e}")
        return templates.TemplateResponse("stats.html", {
            "request": request,
            "active": "stats",
            "total_users": 0,
            "total_qr": 0,
            "total_scans": 0,
            "total_blocked_ips": 0,
            "roles_stats": [],
            "recent_qr": [],
            "scans_stats": [],
            "user": user,
            "error": "Ошибка при загрузке статистики"
        })

# --- ПАНЕЛЬ ПОЛЬЗОВАТЕЛЯ ---
@app.get("/user/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("user_dashboard.html", {
        "request": request,
        "user": user,
        "active": "dashboard"
    })

# --- НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ---
@app.get("/user/settings", response_class=HTMLResponse)
async def user_settings(request: Request):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("user_settings.html", {
        "request": request,
        "user": user,
        "active": "settings"
    })

# --- СМЕНА ТЕМЫ ---
@app.post("/user/settings/theme")
async def change_theme(request: Request, theme: str = Form(...)):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET theme = ? WHERE id = ?",
                (theme, user[0])
            )
            await db.commit()
        
        # Обновляем данные пользователя в сессии
        request.session["user_theme"] = theme
        
        # Логируем смену темы
        await log_action(user[0], "theme_change", f"Смена темы на: {theme}")
        
    except Exception as e:
        logger.error(f"Ошибка при смене темы: {e}")
    
    return RedirectResponse(url="/user/settings", status_code=303)

# --- ЗАГРУЗКА ЛОГОТИПА ---
@app.post("/user/settings/upload_logo")
async def upload_logo(request: Request, logo: UploadFile = File(...)):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        # Проверяем тип файла
        if not logo.content_type.startswith('image/'):
            return RedirectResponse(url="/user/settings?error=invalid_file", status_code=303)
        
        # Генерируем уникальное имя файла
        file_extension = logo.filename.split('.')[-1]
        filename = f"{user[0]}_{uuid.uuid4()}.{file_extension}"
        filepath = os.path.join(LOGOS_FOLDER, filename)
        
        # Сохраняем файл
        with open(filepath, "wb") as buffer:
            content = await logo.read()
            buffer.write(content)
        
        # Обновляем базу данных
        logo_url = f"/static/logos/{filename}"
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET logo_url = ? WHERE id = ?",
                (logo_url, user[0])
            )
            await db.commit()
        
        # Логируем загрузку логотипа
        await log_action(user[0], "logo_upload", "Загружен новый логотип")
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке логотипа: {e}")
        return RedirectResponse(url="/user/settings?error=upload_failed", status_code=303)
    
    return RedirectResponse(url="/user/settings", status_code=303)

# --- СВЯЗЬ С ПОДДЕРЖКОЙ ---
@app.get("/user/contact", response_class=HTMLResponse)
async def user_contact(request: Request):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("user_contact.html", {
        "request": request,
        "user": user,
        "active": "contact"
    })

# --- МОДУЛИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ---
@app.get("/user/modules", response_class=HTMLResponse)
async def user_modules(request: Request):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("user_modules.html", {
        "request": request,
        "user": user,
        "active": "modules"
    })

# --- ПОДМОДУЛИ ---
@app.get("/user/business", response_class=HTMLResponse)
async def user_business(request: Request):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("business.html", {
        "request": request,
        "user": user,
        "active": "business"
    })

@app.get("/user/services", response_class=HTMLResponse)
async def user_services(request: Request):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("services.html", {
        "request": request,
        "user": user,
        "active": "services"
    })

@app.get("/user/cleaning", response_class=HTMLResponse)
async def user_cleaning(request: Request):
    user = await check_user_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("cleaning.html", {
        "request": request,
        "user": user,
        "active": "cleaning"
    })

# --- ПОДМОДУЛИ ДЛЯ АДМИНА ---
@app.get("/dashboard/business", response_class=HTMLResponse)
async def admin_business(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("business.html", {
        "request": request,
        "user": user,
        "active": "business"
    })

@app.get("/dashboard/services", response_class=HTMLResponse)
async def admin_services(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("services.html", {
        "request": request,
        "user": user,
        "active": "services"
    })

@app.get("/dashboard/cleaning", response_class=HTMLResponse)
async def admin_cleaning(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    return templates.TemplateResponse("cleaning.html", {
        "request": request,
        "user": user,
        "active": "cleaning"
    })

# --- ОСТАЛЬНЫЕ МАРШРУТЫ ДЛЯ АДМИНА ---
@app.get("/dashboard/modules", response_class=HTMLResponse)
async def modules(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    return templates.TemplateResponse("modules.html", {
        "request": request, 
        "active": "modules",
        "user": user
    })

@app.get("/dashboard/settings", response_class=HTMLResponse)
async def settings(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    return templates.TemplateResponse("settings.html", {
        "request": request, 
        "active": "settings",
        "user": user
    })

# --- СТРАНИЦЫ ОШИБОК ---
@app.get("/user/blocked", response_class=HTMLResponse)
async def user_blocked(request: Request):
    return templates.TemplateResponse("user_blocked.html", {"request": request})

@app.get("/user/frozen", response_class=HTMLResponse)
async def user_frozen(request: Request):
    return templates.TemplateResponse("account_frozen.html", {"request": request})

@app.get("/ip/blocked", response_class=HTMLResponse)
async def ip_blocked(request: Request):
    return templates.TemplateResponse("ip_blocked.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
