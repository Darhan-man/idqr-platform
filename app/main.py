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

# Секретный ключ для сессий
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

# Настройка безопасности
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
                    user_id INTEGER,
                    qr_type TEXT DEFAULT 'url'
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
                    ip_address TEXT,
                    is_medical_worker BOOLEAN NOT NULL DEFAULT 0
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
            
            # Таблица жалоб
            await db.execute("""
                CREATE TABLE IF NOT EXISTS complaints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT DEFAULT 'new',
                    priority TEXT DEFAULT 'medium',
                    assigned_to INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT
                )
            """)
            
            # Таблица медицинских данных
            await db.execute("""
                CREATE TABLE IF NOT EXISTS medical_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    category TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    value TEXT NOT NULL,
                    date_recorded TEXT NOT NULL,
                    notes TEXT,
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
    if len(password) > 72:
        password = password[:72]
    return pwd_context.hash(password)

async def check_ip_blocked(ip_address: str) -> bool:
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
                    if datetime.now() < datetime.fromisoformat(blocked_until):
                        return True
                    else:
                        await db.execute("DELETE FROM blocked_ips WHERE ip_address = ?", (ip_address,))
                        await db.commit()
                        return False
                else:
                    return True
    except Exception as e:
        logger.error(f"Ошибка при проверке IP: {e}")
    
    return False

async def log_action(user_id: int, action_type: str, description: str, ip_address: str = None):
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
        if await check_ip_blocked(ip_address):
            return {"error": "ip_blocked", "message": "Ваш IP-адрес заблокирован"}
        
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT * FROM users WHERE username = ?", 
                (username,)
            )
            user = await cursor.fetchone()
            
        if user:
            if user[7]:
                return {"error": "blocked", "message": "Ваш аккаунт заблокирован за нарушения"}
            
            if user[8]:
                freeze_until = datetime.fromisoformat(user[8])
                if datetime.now() < freeze_until:
                    return {
                        "error": "frozen", 
                        "message": f"Ваш аккаунт заморожен за нарушения. Разблокировка через: {freeze_until.strftime('%d.%m.%Y %H:%M')}"
                    }
                else:
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "UPDATE users SET frozen_until = NULL WHERE id = ?",
                            (user[0],)
                        )
                        await db.commit()
            
            if not user[4]:
                return {"error": "inactive", "message": "Ваш аккаунт деактивирован"}
            
            if verify_password(password, user[2]):
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE users SET ip_address = ?, last_login = ? WHERE id = ?",
                        (ip_address, datetime.now().isoformat(), user[0])
                    )
                    await db.commit()
                
                await log_action(user[0], "login", "Успешный вход в систему", ip_address)
                return user
        
        await log_action(None, "failed_login", f"Неудачная попытка входа для пользователя {username}", ip_address)
        return None
    except Exception as e:
        logger.error(f"Ошибка аутентификации: {e}")
        return None

async def get_current_user(request: Request):
    try:
        user_id = request.session.get("user_id")
        client_ip = request.client.host
        
        if await check_ip_blocked(client_ip):
            return {"error": "ip_blocked", "message": "Ваш IP-адрес заблокирован"}
        
        if user_id:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                user = await cursor.fetchone()
            
            if user:
                if user[7]:
                    return {"error": "blocked", "message": "Ваш аккаунт заблокирован за нарушения"}
                
                if user[8]:
                    freeze_until = datetime.fromisoformat(user[8])
                    if datetime.now() < freeze_until:
                        return {
                            "error": "frozen", 
                            "message": f"Ваш аккаунт заморожен за нарушения. Разблокировка через: {freeze_until.strftime('%d.%m.%Y %H:%M')}"
                        }
                    else:
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute(
                                "UPDATE users SET frozen_until = NULL WHERE id = ?",
                                (user[0],)
                            )
                            await db.commit()
                
                if not user[4]:
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
    module = request.query_params.get("module")
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'registration_enabled'")
            result = await cursor.fetchone()
            registration_enabled = result[0] == 'true' if result else True
            
        if not registration_enabled:
            return templates.TemplateResponse("register.html", {
                "request": request, 
                "error": "Регистрация новых пользователей временно отключена",
                "module": module
            })
    except Exception as e:
        logger.error(f"Ошибка при проверке настроек регистрации: {e}")
    
    return templates.TemplateResponse("register.html", {
        "request": request,
        "module": module
    })

@app.post("/register")
async def register(
    request: Request, 
    username: str = Form(...), 
    password: str = Form(...),
    is_medical_worker: str = Form("off"),
    module: Optional[int] = Form(None)
):
    client_ip = get_client_ip(request)
    
    if await check_ip_blocked(client_ip):
        return templates.TemplateResponse("register.html", {
            "request": request, 
            "error": "Ваш IP-адрес заблокирован. Регистрация невозможна.",
            "module": module
        })
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT setting_value FROM system_settings WHERE setting_key = 'registration_enabled'")
            result = await cursor.fetchone()
            registration_enabled = result[0] == 'true' if result else True
            
        if not registration_enabled:
            return templates.TemplateResponse("register.html", {
                "request": request, 
                "error": "Регистрация новых пользователей временно отключена",
                "module": module
            })
    except Exception as e:
        logger.error(f"Ошибка при проверке настроек регистрации: {e}")
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id FROM users WHERE username = ?", (username,))
            existing_user = await cursor.fetchone()
            
            if existing_user:
                return templates.TemplateResponse("register.html", {
                    "request": request, 
                    "error": "Пользователь с таким именем уже существует",
                    "module": module
                })
            
            password_hash = get_password_hash(password)
            created_at = datetime.now().isoformat()
            is_medical = 1 if is_medical_worker == "on" else 0
            
            await db.execute(
                """INSERT INTO users 
                (username, password_hash, role, created_at, ip_address, is_medical_worker) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (username, password_hash, "user", created_at, client_ip, is_medical)
            )
            await db.commit()
            
            cursor = await db.execute("SELECT id FROM users WHERE username = ?", (username,))
            new_user = await cursor.fetchone()
            
            await log_action(new_user[0], "registration", f"Новый пользователь зарегистрирован. Медицинский работник: {is_medical}", client_ip)
            
        if module:
            return RedirectResponse(url=f"/modules", status_code=303)
        else:
            return RedirectResponse(url="/user/login", status_code=303)
        
    except Exception as e:
        logger.error(f"Ошибка при регистрации: {e}")
        return templates.TemplateResponse("register.html", {
            "request": request, 
            "error": "Ошибка при регистрации",
            "module": module
        })

# --- ГЛАВНАЯ СТРАНИЦА ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    client_ip = get_client_ip(request)
    
    if await check_ip_blocked(client_ip):
        return templates.TemplateResponse("ip_blocked.html", {
            "request": request,
            "message": "Ваш IP-адрес заблокирован"
        })
    
    user = await get_current_user(request)
    if user and not isinstance(user, dict):
        if user[3] == "admin":
            return RedirectResponse(url="/dashboard/qr", status_code=303)
        else:
            return RedirectResponse(url="/user/dashboard", status_code=303)
    elif isinstance(user, dict):
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
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM users WHERE username = 'admin'")
            admin_user = await cursor.fetchone()
        
        if admin_user:
            request.session["user_id"] = admin_user[0]
            request.session["user_role"] = admin_user[3]
            
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
    module = request.query_params.get("module")
    return templates.TemplateResponse("user_login.html", {
        "request": request,
        "module": module
    })

@app.post("/user/login", response_class=HTMLResponse)
async def user_login(
    request: Request, 
    username: str = Form(...), 
    password: str = Form(...),
    module: Optional[int] = Form(None)
):
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
                "error": result["message"],
                "module": module
            })
    elif result:
        request.session["user_id"] = result[0]
        request.session["user_role"] = result[3]
        
        if module:
            return RedirectResponse(url=f"/scan/modules/{module}", status_code=303)
        else:
            return RedirectResponse(url="/modules", status_code=303)
    
    return templates.TemplateResponse("user_login.html", {
        "request": request, 
        "error": "Неверный логин или пароль",
        "module": module
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

async def check_user_access(request: Request):
    user = await get_current_user(request)
    if isinstance(user, dict):
        return user
    if not user:
        return RedirectResponse(url="/user/login", status_code=303)
    return user

async def check_ip_access(request: Request):
    user = await get_current_user(request)
    if isinstance(user, dict):
        return user
    if not user:
        return RedirectResponse(url="/user/login", status_code=303)
    if user[3] not in ["ip", "admin"]:
        return RedirectResponse(url="/user/dashboard", status_code=303)
    return user

# --- QR-КОДЫ ---
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

# --- Генерация QR ---
@app.post("/generate_qr")
async def generate_qr(
    request: Request, 
    qrdata: str = Form(...), 
    title: str = Form(...),
    qr_color: str = Form("#000000"),
    text_color: str = Form("#000000"),
    qr_type: str = Form("url")
):
    user = await check_ip_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
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
        
        # Определяем конечный URL в зависимости от типа QR-кода
        if qr_type == "module":
            try:
                module_id = int(qrdata)
                if 1 <= module_id <= 19:
                    # Сохраняем ID модуля в базу
                    data = str(module_id)
                else:
                    return templates.TemplateResponse("qr.html", {
                        "request": request,
                        "error": "ID модуля должен быть от 1 до 19",
                        "user": user
                    })
            except ValueError:
                return templates.TemplateResponse("qr.html", {
                    "request": request,
                    "error": "Для типа 'модуль' необходимо ввести ID модуля (число от 1 до 19)",
                    "user": user
                })
        else:
            data = qrdata
        
        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(QR_FOLDER, filename)

        colors_json = json.dumps({
            "qr_color": qr_color,
            "bg_color": "#FFFFFF",
            "text_color": text_color
        })

        async with aiosqlite.connect(DB_PATH) as db:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = await db.execute(
                "INSERT INTO qr_codes (title, data, filename, created_at, colors, user_id, qr_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, data, filename, now, colors_json, user[0], qr_type)
            )
            await db.commit()
            qr_id = cursor.lastrowid

        # Генерируем QR-код со ссылкой на сканирование
        if qr_type == "module":
            scan_url = f"{BASE_URL}/scan/{qr_id}"
        else:
            scan_url = data
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(scan_url)
        qr.make(fit=True)
        
        qr_img = qr.make_image(fill_color=qr_color, back_color="white").convert("RGB")
        
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

        await log_action(user[0], "qr_create", f"Создан QR-код: {title} (тип: {qr_type})")
        
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    
    except Exception as e:
        logger.error(f"Ошибка при генерации QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- СКАНИРОВАНИЕ QR С ВЫБОРОМ ДОСТУПА ---
@app.get("/scan/{qr_id}")
async def scan_qr(qr_id: int, request: Request):
    """Страница выбора способа доступа после сканирования QR-кода"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT data, scan_count, qr_type FROM qr_codes WHERE id = ?", (qr_id,))
            row = await cursor.fetchone()
            
            if row:
                data, scan_count, qr_type = row
                
                # Увеличиваем счетчик сканирований
                await db.execute(
                    "UPDATE qr_codes SET scan_count = ?, last_scan = ? WHERE id = ?",
                    (scan_count + 1, datetime.now().isoformat(), qr_id)
                )
                await db.commit()
                
                if qr_type == "module":
                    # Получаем ID модуля и показываем страницу выбора доступа
                    module_id = int(data)
                    
                    modules = {
                        1: "Услуги и быт",
                        2: "Одежда и мода",
                        3: "Транспорт и авто",
                        4: "Образование и школы",
                        5: "Медицина и здоровье",
                        6: "Стройка и объекты",
                        7: "Бизнес и магазины",
                        8: "Склад и логистика",
                        9: "ЖКХ и дома",
                        10: "События и вход",
                        11: "Документы и удостоверения",
                        12: "Госуслуги и учет",
                        13: "Безопасность и контроль",
                        14: "Реклама и аналитика",
                        15: "Курсы и тренинги",
                        16: "Подарки и сервис",
                        17: "Маркетинг и бренды",
                        18: "Квитанции и оплата",
                        19: "Энергетика и инфраструктура"
                    }
                    
                    module_name = modules.get(module_id, f"Модуль #{module_id}")
                    
                    return templates.TemplateResponse("module_access.html", {
                        "request": request,
                        "module_id": module_id,
                        "module_name": module_name
                    })
                else:
                    # Если это обычная ссылка, перенаправляем на нее
                    return RedirectResponse(data)
        
        return RedirectResponse("/", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при сканировании QR-кода: {e}")
        return RedirectResponse("/", status_code=303)

# --- ГОСТЕВОЙ ДОСТУП К МОДУЛЮ ---
@app.get("/module/{module_id}/guest", response_class=HTMLResponse)
async def guest_module_access(request: Request, module_id: int):
    """Гостевой доступ к модулю"""
    try:
        modules = {
            1: ("Услуги и быт", "/modules/services"),
            2: ("Одежда и мода", "/modules/clothing"),
            3: ("Транспорт и авто", "/modules/transport"),
            4: ("Образование и школы", "/modules/education"),
            5: ("Медицина и здоровье", "/modules/medicine"),
            6: ("Стройка и объекты", "/modules/construction"),
            7: ("Бизнес и магазины", "/modules/business"),
            8: ("Склад и логистика", "/modules/logistics"),
            9: ("ЖКХ и дома", "/modules/housing"),
            10: ("События и вход", "/modules/events"),
            11: ("Документы и удостоверения", "/modules/docs"),
            12: ("Госуслуги и учет", "/modules/gov"),
            13: ("Безопасность и контроль", "/modules/security"),
            14: ("Реклама и аналитика", "/modules/ads"),
            15: ("Курсы и тренинги", "/modules/courses"),
            16: ("Подарки и сервис", "/modules/gifts"),
            17: ("Маркетинг и бренды", "/modules/branding"),
            18: ("Квитанции и оплата", "/modules/payment"),
            19: ("Энергетика и инфраструктура", "/modules/energy")
        }
        
        if module_id not in modules:
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error": "Модуль не найден"
            })
        
        module_name, module_url = modules[module_id]
        
        return templates.TemplateResponse("guest_module.html", {
            "request": request,
            "module_id": module_id,
            "module_name": module_name,
            "module_url": module_url
        })
    except Exception as e:
        logger.error(f"Ошибка при гостевом доступе к модулю: {e}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Ошибка при загрузке модуля"
        })

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
                
            if user[3] != "admin" and qr_code[8] != user[0]:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
            
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
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,))
            old_qr = await cursor.fetchone()
            
            if not old_qr:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
                
            if user[3] != "admin" and old_qr[8] != user[0]:
                return RedirectResponse(url="/dashboard/qr", status_code=303)
            
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

        await log_action(user[0], "qr_update", f"Обновлен QR-код: {title}")
        
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    
    except Exception as e:
        logger.error(f"Ошибка при обновлении QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- УДАЛЕНИЕ QR ---
@app.get("/delete_qr/{qr_id}")
async def delete_qr(request: Request, qr_id: int):
    user = await check_ip_access(request)
    if isinstance(user, RedirectResponse) or isinstance(user, dict):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
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
                    
                    await log_action(user[0], "qr_delete", f"Удален QR-код: {qr_owner[1]}")
        
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при удалении QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- СТРАНИЦА МОДУЛЕЙ (доступна всем) ---
@app.get("/modules", response_class=HTMLResponse)
async def modules_page(request: Request):
    """Страница с выбором модулей (доступна всем)"""
    return templates.TemplateResponse("modules.html", {
        "request": request
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

# --- ДОБАВЛЕННЫЕ МАРШРУТЫ ДЛЯ HTML-ФАЙЛОВ ---

# Страница ошибки
@app.get("/error", response_class=HTMLResponse)
async def error_page(request: Request, error: str = None):
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": error
    })

# Страница замороженного аккаунта
@app.get("/account_frozen", response_class=HTMLResponse)
async def account_frozen_page(request: Request):
    return templates.TemplateResponse("account_frozen.html", {"request": request})

# Страница заблокированного IP
@app.get("/ip_blocked", response_class=HTMLResponse)
async def ip_blocked_page(request: Request):
    return templates.TemplateResponse("ip_blocked.html", {"request": request})

# Страница модулей
@app.get("/modules", response_class=HTMLResponse)
async def modules_page(request: Request):
    return templates.TemplateResponse("modules.html", {"request": request})

# Гостевой доступ к модулю
@app.get("/guest_module/{module_id}", response_class=HTMLResponse)
async def guest_module_page(request: Request, module_id: int):
    return templates.TemplateResponse("guest_module.html", {
        "request": request,
        "module_id": module_id
    })

# Выбор доступа к модулю
@app.get("/module_access/{qr_id}", response_class=HTMLResponse)
async def module_access_page(request: Request, qr_id: int):
    return templates.TemplateResponse("module_access.html", {
        "request": request,
        "qr_id": qr_id
    })

# Страница регистрации
@app.get("/register_page", response_class=HTMLResponse)
async def register_page_route(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

# Страница входа
@app.get("/login_page", response_class=HTMLResponse)
async def login_page_route(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

# Страница QR кодов
@app.get("/qr_page", response_class=HTMLResponse)
async def qr_page_route(request: Request):
    return templates.TemplateResponse("qr.html", {"request": request})

# --- МАРШРУТЫ ИЗ СКРИНОВ 1000040615.jpg ---

@app.get("/energy_meters", response_class=HTMLResponse)
async def energy_meters_page(request: Request):
    return templates.TemplateResponse("energy_meters.html", {"request": request})

@app.get("/energy_renewable", response_class=HTMLResponse)
async def energy_renewable_page(request: Request):
    return templates.TemplateResponse("energy_renewable.html", {"request": request})

@app.get("/energy_suppliers", response_class=HTMLResponse)
async def energy_suppliers_page(request: Request):
    return templates.TemplateResponse("energy_suppliers.html", {"request": request})

@app.get("/energy", response_class=HTMLResponse)
async def energy_page(request: Request):
    return templates.TemplateResponse("energy.html", {"request": request})

@app.get("/index_page", response_class=HTMLResponse)
async def index_page_route(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/ip_management", response_class=HTMLResponse)
async def ip_management_page(request: Request):
    return templates.TemplateResponse("ip_management.html", {"request": request})

@app.get("/medicine", response_class=HTMLResponse)
async def medicine_page(request: Request):
    return templates.TemplateResponse("medicine.html", {"request": request})

@app.get("/services", response_class=HTMLResponse)
async def services_page(request: Request):
    return templates.TemplateResponse("services.html", {"request": request})

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})

@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    return templates.TemplateResponse("stats.html", {"request": request})

# --- МАРШРУТЫ ИЗ СКРИНОВ 1000040613.jpg ---

@app.get("/business", response_class=HTMLResponse)
async def business_page(request: Request):
    return templates.TemplateResponse("business.html", {"request": request})

@app.get("/cleaning", response_class=HTMLResponse)
async def cleaning_page(request: Request):
    return templates.TemplateResponse("cleaning.html", {"request": request})

@app.get("/complaint_form", response_class=HTMLResponse)
async def complaint_form_page(request: Request):
    return templates.TemplateResponse("complaint_form.html", {"request": request})

@app.get("/complaint_status", response_class=HTMLResponse)
async def complaint_status_page(request: Request):
    return templates.TemplateResponse("complaint_status.html", {"request": request})

@app.get("/complaint_success", response_class=HTMLResponse)
async def complaint_success_page(request: Request):
    return templates.TemplateResponse("complaint_success.html", {"request": request})

@app.get("/edit_qr", response_class=HTMLResponse)
async def edit_qr_page_route(request: Request):
    return templates.TemplateResponse("edit_qr.html", {"request": request})

@app.get("/energy_analytics", response_class=HTMLResponse)
async def energy_analytics_page(request: Request):
    return templates.TemplateResponse("energy_analytics.html", {"request": request})

@app.get("/energy_complaints", response_class=HTMLResponse)
async def energy_complaints_page(request: Request):
    return templates.TemplateResponse("energy_complaints.html", {"request": request})

@app.get("/energy_documents", response_class=HTMLResponse)
async def energy_documents_page(request: Request):
    return templates.TemplateResponse("energy_documents.html", {"request": request})

@app.get("/energy_electricity", response_class=HTMLResponse)
async def energy_electricity_page(request: Request):
    return templates.TemplateResponse("energy_electricity.html", {"request": request})

@app.get("/energy_heat_gas", response_class=HTMLResponse)
async def energy_heat_gas_page(request: Request):
    return templates.TemplateResponse("energy_heat_gas.html", {"request": request})

@app.get("/energy_inspections", response_class=HTMLResponse)
async def energy_inspections_page(request: Request):
    return templates.TemplateResponse("energy_inspections.html", {"request": request})

# --- МАРШРУТЫ ИЗ СКРИНОВ 1000040616.jpg ---

@app.get("/system_logs", response_class=HTMLResponse)
async def system_logs_page(request: Request):
    return templates.TemplateResponse("system_logs.html", {"request": request})

@app.get("/system_settings", response_class=HTMLResponse)
async def system_settings_page(request: Request):
    return templates.TemplateResponse("system_settings.html", {"request": request})

@app.get("/user_contact", response_class=HTMLResponse)
async def user_contact_page(request: Request):
    return templates.TemplateResponse("user_contact.html", {"request": request})

@app.get("/user_dashboard_1", response_class=HTMLResponse)
async def user_dashboard_1_page(request: Request):
    return templates.TemplateResponse("user_dashboard-1.html", {"request": request})

@app.get("/user_dashboard", response_class=HTMLResponse)
async def user_dashboard_page_route(request: Request):
    return templates.TemplateResponse("user_dashboard.html", {"request": request})

@app.get("/user_energy", response_class=HTMLResponse)
async def user_energy_page(request: Request):
    return templates.TemplateResponse("user_energy.html", {"request": request})

@app.get("/user_login", response_class=HTMLResponse)
async def user_login_page_route(request: Request):
    return templates.TemplateResponse("user_login.html", {"request": request})

@app.get("/user_medicine", response_class=HTMLResponse)
async def user_medicine_page(request: Request):
    return templates.TemplateResponse("user_medicine.html", {"request": request})

@app.get("/user_modules", response_class=HTMLResponse)
async def user_modules_page_route(request: Request):
    return templates.TemplateResponse("user_modules.html", {"request": request})

@app.get("/user_settings", response_class=HTMLResponse)
async def user_settings_page(request: Request):
    return templates.TemplateResponse("user_settings.html", {"request": request})

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    return templates.TemplateResponse("users.html", {"request": request})

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