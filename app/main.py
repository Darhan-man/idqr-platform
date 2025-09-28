from fastapi import FastAPI, Form, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
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
from starlette.middleware.sessions import SessionMiddleware

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Настройка сессий с поддержкой HTTPS (для Render)
SECRET_KEY = secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=3600, same_site="lax")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Константы ---
QR_FOLDER = "static/qr"
DB_PATH = "qr_data.db"
ADMIN_CODE = "admin1990"
BASE_URL = "https://idqr-platform.onrender.com"

# Настройка безопасности
security = HTTPBasic()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Создаем папки если они не существуют
os.makedirs(QR_FOLDER, exist_ok=True)
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
                    user_id INTEGER DEFAULT 1
                )
            """)
            
            # Таблица пользователей с полями для блокировки и заморозки
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
                    frozen_until TEXT
                )
            """)
            
            # Проверяем, существует ли admin, если нет — создаём
            cursor = await db.execute("SELECT id FROM users WHERE username = 'admin'")
            admin_exists = await cursor.fetchone()
            
            if not admin_exists:
                # Создаём admin заново, с усечением пароля для bcrypt
                default_password = "admin123"[:72]  # Усечение до 72 байт
                password_hash = pwd_context.hash(default_password)
                await db.execute("""
                    INSERT INTO users (username, password_hash, role, created_at, is_active) 
                    VALUES (?, ?, ?, ?, ?)
                """, ("admin", password_hash, "admin", datetime.now().isoformat(), 1))
                logger.info("Admin user created successfully")
            else:
                logger.info("Admin user already exists")
            
            await db.commit()
            
            # Финальная проверка
            cursor = await db.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
            count = (await cursor.fetchone())[0]
            logger.info(f"База данных инициализирована. Admin user exists: {count > 0}")
            
    except Exception as e:
        logger.error(f"Ошибка при инициализации БД: {e}")

# --- Функции аутентификации ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    # Усечение пароля до 72 байт для bcrypt
    truncated_password = password[:72]
    return pwd_context.hash(truncated_password)

async def authenticate_user(username: str, password: str):
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT * FROM users WHERE username = ?", 
                (username,)
            )
            user = await cursor.fetchone()
            
        if user:
            # Проверка блокировки
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
            
            # Проверка пароля (усечение для верификации)
            truncated_password = password[:72]
            if verify_password(truncated_password, user[2]):
                return user
        
        return None
    except Exception as e:
        logger.error(f"Ошибка аутентификации: {e}")
        return None

async def get_current_user(request: Request):
    user_id = request.session.get("user_id")
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

# --- ГЛАВНАЯ (автоматически в админку) ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Проверяем, авторизован ли пользователь
    user = await get_current_user(request)
    if user and not isinstance(user, dict):
        if user[3] == "admin":  # role
            return RedirectResponse(url="/dashboard/qr", status_code=303)
        else:
            return RedirectResponse(url="/user/dashboard", status_code=303)
    elif isinstance(user, dict):
        # Пользователь заблокирован или заморожен - показываем сообщение
        return templates.TemplateResponse("user_blocked.html", {
            "request": request,
            "message": user["message"]
        })
    
    # Если не авторизован - показываем вход в админку
    return templates.TemplateResponse("index.html", {"request": request})

# --- ВХОД АДМИНИСТРАТОРА ---
@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    logger.info(f"Received code: '{code}'")  # Для дебага в логах Render
    if code == ADMIN_CODE:
        # Находим пользователя admin
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT * FROM users WHERE username = 'admin'")
                admin_user = await cursor.fetchone()
                logger.info(f"Admin user found: {admin_user is not None}")
            
            if admin_user:
                request.session["user_id"] = admin_user[0]
                logger.info(f"Session set for user_id: {admin_user[0]}")
                return RedirectResponse(url="/dashboard/qr", status_code=303)
            else:
                logger.error("Admin user not found in DB")
                # Временный хак: создаём admin на лету, если не найден, с усечением
                try:
                    default_password = "admin123"[:72]  # Усечение до 72 байт
                    password_hash = pwd_context.hash(default_password)
                    async with aiosqlite.connect(DB_PATH) as db:
                        cursor = await db.execute("""
                            INSERT INTO users (username, password_hash, role, created_at, is_active) 
                            VALUES (?, ?, ?, ?, ?)
                        """, ("admin", password_hash, "admin", datetime.now().isoformat(), 1))
                        await db.commit()
                        admin_id = cursor.lastrowid
                        logger.info(f"Admin user created on-the-fly with ID: {admin_id}")
                        request.session["user_id"] = admin_id
                        return RedirectResponse(url="/dashboard/qr", status_code=303)
                except Exception as create_e:
                    logger.error(f"Failed to create admin on-the-fly: {create_e}")
        except Exception as e:
            logger.error(f"Error fetching admin user: {e}")
    
    return templates.TemplateResponse("index.html", {"request": request, "error": "Неверный код"})

# --- ВХОД ПОЛЬЗОВАТЕЛЯ ---
@app.get("/user/login", response_class=HTMLResponse)
async def user_login_page(request: Request):
    return templates.TemplateResponse("user_login.html", {"request": request})

@app.post("/user/login", response_class=HTMLResponse)
async def user_login(request: Request, username: str = Form(...), password: str = Form(...)):
    result = await authenticate_user(username, password)
    
    if isinstance(result, dict) and "error" in result:
        return templates.TemplateResponse("user_login.html", {
            "request": request, 
            "error": result["message"]
        })
    elif result:
        request.session["user_id"] = result[0]
        # Обновляем время последнего входа
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (datetime.now().isoformat(), result[0])
            )
            await db.commit()
        return RedirectResponse(url="/user/dashboard", status_code=303)
    
    return templates.TemplateResponse("user_login.html", {
        "request": request, 
        "error": "Неверный логин или пароль"
    })

# --- ВЫХОД ---
@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)

# --- ПАНЕЛЬ АДМИНИСТРАТОРА ---
async def check_admin(request: Request):
    user = await get_current_user(request)
    if isinstance(user, dict):
        return RedirectResponse(url="/", status_code=303)
    if not user or user[3] != "admin":
        return RedirectResponse(url="/", status_code=303)
    return user

@app.get("/dashboard/qr", response_class=HTMLResponse)
async def dashboard_qr(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
            qr_list = await cursor.fetchall()
        return templates.TemplateResponse("qr.html", {
            "request": request,
            "qr_list": qr_list,
            "qr_url": None,
            "qr_title": None,
            "active": "qr"
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке QR-кодов: {e}")
        return templates.TemplateResponse("qr.html", {
            "request": request,
            "qr_list": [],
            "qr_url": None,
            "qr_title": None,
            "active": "qr",
            "error": "Ошибка при загрузке данных"
        })

# --- ПРОСМОТР ОТДЕЛЬНОГО QR ---
@app.get("/dashboard/qr/view/{qr_id}", response_class=HTMLResponse)
async def view_qr(request: Request, qr_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
            qr_list = await cursor.fetchall()
            cursor = await db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,))
            row = await cursor.fetchone()
        
        if row:
            qr_url = f"/static/qr/{row[3]}"
            qr_title = row[1]
            
            # Проверяем существует ли файл
            if not os.path.exists(os.path.join(QR_FOLDER, row[3])):
                logger.warning(f"Файл QR-кода не найден: {row[3]}")
                qr_url = None
        else:
            qr_url = None
            qr_title = None
            
        return templates.TemplateResponse("qr.html", {
            "request": request,
            "qr_list": qr_list,
            "qr_url": qr_url,
            "qr_title": qr_title,
            "active": "qr"
        })
    except Exception as e:
        logger.error(f"Ошибка при просмотре QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- Генерация QR (СОЗДАНИЕ, POST) ---
@app.post("/generate_qr")
async def generate_qr(
    request: Request, 
    qrdata: str = Form(...), 
    title: str = Form(...),
    qr_color: str = Form("#000000"),
    text_color: str = Form("#000000")
):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        # Генерируем уникальное имя файла
        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(QR_FOLDER, filename)

        # Сохраняем цвета в формате JSON
        colors_json = json.dumps({
            "qr_color": qr_color,
            "bg_color": "#FFFFFF",  # Белый фон по умолчанию
            "text_color": text_color
        })

        # Сначала создаем запись в БД
        async with aiosqlite.connect(DB_PATH) as db:
            now = datetime.now().isoformat()
            cursor = await db.execute(
                "INSERT INTO qr_codes (title, data, filename, created_at, colors) VALUES (?, ?, ?, ?, ?)",
                (title, qrdata, filename, now, colors_json)
            )
            await db.commit()
            qr_id = cursor.lastrowid
            logger.info(f"Создана запись в БД с ID: {qr_id}")

        # Генерируем QR-код
        scan_url = f"{BASE_URL}/scan/{qr_id}"
        
        # Создаем QR-код с выбранными цветами
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(scan_url)
        qr.make(fit=True)
        
        # Создаем изображение QR-кода с белым фоном и выбранным цветом
        qr_img = qr.make_image(fill_color=qr_color, back_color="white").convert("RGB")
        
        # Добавляем текст поверх QR-кода
        try:
            # Пытаемся использовать шрифт RobotoSlab
            font = ImageFont.truetype("static/fonts/RobotoSlab-Bold.ttf", 28)
        except IOError:
            # Если шрифт не найден, используем стандартный
            font = ImageFont.load_default()
            logger.warning("Шрифт RobotoSlab-Bold.ttf не найден, используется стандартный шрифт")
        
        # Разбиваем текст на строки по 20 символов
        max_chars_per_line = 20
        wrapped_text = textwrap.fill(title, width=max_chars_per_line)
        lines = wrapped_text.split('\n')
        
        # Рассчитываем высоту текста
        line_height = 30
        text_height = len(lines) * line_height + 20
        
        # Создаем новое изображение с белым фоном и местом для текста
        new_img = Image.new("RGB", (qr_img.width, qr_img.height + text_height), "white")
        new_img.paste(qr_img, (0, text_height))
        
        # Рисуем текст
        draw = ImageDraw.Draw(new_img)
        y = 10
        for line in lines:
            text_bbox = draw.textbbox((0, 0), line, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_x = (new_img.width - text_width) // 2
            draw.text((text_x, y), line, font=font, fill=text_color)
            y += line_height
        
        # Сохраняем изображение
        new_img.save(filepath)
        logger.info(f"QR-код сохранен в файл: {filepath}")

        # Перенаправляем на страницу просмотра
        return RedirectResponse(url=f"/dashboard/qr/view/{qr_id}", status_code=303)
    
    except Exception as e:
        logger.error(f"Ошибка при генерации QR-кода: {e}")
        
        # Удаляем запись из БД, если она была создана
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM qr_codes WHERE filename = ?", (filename,))
                await db.commit()
        except:
            pass
            
        return templates.TemplateResponse("qr.html", {
            "request": request,
            "qr_list": [],
            "qr_url": None,
            "qr_title": None,
            "active": "qr",
            "error": f"Ошибка при генерации QR-кода: {str(e)}"
        })

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
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT filename FROM qr_codes WHERE id = ?", (qr_id,))
            row = await cursor.fetchone()
            if row:
                filename = row[0]
                path = os.path.join(QR_FOLDER, filename)
                if os.path.exists(path):
                    os.remove(path)
                await db.execute("DELETE FROM qr_codes WHERE id = ?", (qr_id,))
                await db.commit()
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при удалении QR-кода: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- РЕДАКТИРОВАНИЕ QR ---
@app.get("/dashboard/qr/edit/{qr_id}", response_class=HTMLResponse)
async def edit_qr(request: Request, qr_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,))
            row = await cursor.fetchone()
            
            if row:
                # Парсим цвета из JSON
                colors = json.loads(row[7]) if row[7] else {
                    "qr_color": "#000000",
                    "bg_color": "#FFFFFF",
                    "text_color": "#000000"
                }
                
                return templates.TemplateResponse("edit_qr.html", {
                    "request": request,
                    "qr": row,
                    "colors": colors,
                    "active": "qr"
                })
            
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    except Exception as e:
        logger.error(f"Ошибка при загрузке формы редактирования: {e}")
        return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- ОБНОВЛЕНИЕ QR ---
@app.post("/update_qr/{qr_id}")
async def update_qr(
    request: Request, 
    qr_id: int,
    qrdata: str = Form(...), 
    title: str = Form(...),
    qr_color: str = Form("#000000"),
    text_color: str = Form("#000000")
):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        # Сохраняем цвета в формате JSON
        colors_json = json.dumps({
            "qr_color": qr_color,
            "bg_color": "#FFFFFF",
            "text_color": text_color
        })

        # Обновляем данные в БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE qr_codes SET title = ?, data = ?, colors = ? WHERE id = ?",
                (title, qrdata, colors_json, qr_id)
            )
            await db.commit()
            
            # Получаем имя файла для перегенерации QR-кода
            cursor = await db.execute("SELECT filename FROM qr_codes WHERE id = ?", (qr_id,))
            row = await cursor.fetchone()
            filename = row[0] if row else None

        # Если есть файл, перегенерируем QR-код
        if filename:
            filepath = os.path.join(QR_FOLDER, filename)
            
            # Генерируем QR-код
            scan_url = f"{BASE_URL}/scan/{qr_id}"
            
            # Создаем QR-код с выбранными цветами
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(scan_url)
            qr.make(fit=True)
            
            # Создаем изображение QR-кода с белым фоном
            qr_img = qr.make_image(fill_color=qr_color, back_color="white").convert("RGB")
            
            # Добавляем текст поверх QR-кода
            try:
                font = ImageFont.truetype("static/fonts/RobotoSlab-Bold.ttf", 28)
            except IOError:
                font = ImageFont.load_default()
            
            # Разбиваем текст на строки
            max_chars_per_line = 20
            wrapped_text = textwrap.fill(title, width=max_chars_per_line)
            lines = wrapped_text.split('\n')
            
            # Рассчитываем высоту текста
            line_height = 30
            text_height = len(lines) * line_height + 20
            
            # Создаем новое изображение с белым фоном
            new_img = Image.new("RGB", (qr_img.width, qr_img.height + text_height), "white")
            new_img.paste(qr_img, (0, text_height))
            
            # Рисуем текст
            draw = ImageDraw.Draw(new_img)
            y = 10
            for line in lines:
                text_bbox = draw.textbbox((0, 0), line, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_x = (new_img.width - text_width) // 2
                draw.text((text_x, y), line, font=font, fill=text_color)
                y += line_height
            
            # Сохраняем изображение
            new_img.save(filepath)

        return RedirectResponse(url=f"/dashboard/qr/view/{qr_id}", status_code=303)
    
    except Exception as e:
        logger.error(f"Ошибка при обновлении QR-кода: {e}")
        return RedirectResponse(url=f"/dashboard/qr/edit/{qr_id}", status_code=303)

# --- ПАНЕЛЬ ПОЛЬЗОВАТЕЛЯ ---
async def check_user(request: Request):
    user = await get_current_user(request)
    if isinstance(user, dict):
        # Пользователь заблокирован или заморожен
        return templates.TemplateResponse("user_blocked.html", {
            "request": request,
            "message": user["message"]
        })
    if not user:
        return RedirectResponse(url="/user/login", status_code=303)
    return user

@app.get("/user/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request):
    user_resp = await check_user(request)
    if not isinstance(user_resp, tuple):
        return user_resp
    
    return templates.TemplateResponse("user_dashboard.html", {
        "request": request,
        "user": user_resp
    })

# --- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (только для админа) ---
@app.get("/dashboard/users", response_class=HTMLResponse)
async def users_management(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM users ORDER BY id DESC")
            users_list = await cursor.fetchall()
        
        return templates.TemplateResponse("users.html", {
            "request": request,
            "active": "users",
            "users_list": users_list
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке пользователей: {e}")
        return templates.TemplateResponse("users.html", {
            "request": request,
            "active": "users",
            "users_list": [],
            "error": "Ошибка при загрузке данных"
        })

# --- ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯ ---
@app.post("/dashboard/users/add")
async def add_user(request: Request, username: str = Form(...), password: str = Form(...)):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        password_hash = get_password_hash(password)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'user', ?)",
                (username, password_hash, datetime.now().isoformat())
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при добавлении пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- БЛОКИРОВКА/РАЗБЛОКИРОВКА ПОЛЬЗОВАТЕЛЯ ---
@app.get("/dashboard/users/block/{user_id}")
async def block_user(request: Request, user_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET is_blocked = 1, frozen_until = NULL WHERE id = ? AND role != 'admin'",
                (user_id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при блокировке пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

@app.get("/dashboard/users/unblock/{user_id}")
async def unblock_user(request: Request, user_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET is_blocked = 0 WHERE id = ?",
                (user_id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при разблокировке пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- ЗАМОРОЗКА/РАЗМОРОЗКА ПОЛЬЗОВАТЕЛЯ ---
@app.get("/dashboard/users/freeze/{user_id}")
async def freeze_user(request: Request, user_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        # Заморозка на 7 дней
        freeze_until = (datetime.now() + timedelta(days=7)).isoformat()
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET frozen_until = ?, is_blocked = 0 WHERE id = ? AND role != 'admin'",
                (freeze_until, user_id)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при заморозке пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

@app.get("/dashboard/users/unfreeze/{user_id}")
async def unfreeze_user(request: Request, user_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET frozen_until = NULL WHERE id = ?",
                (user_id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при разморозке пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- АКТИВАЦИЯ/ДЕАКТИВАЦИЯ ---
@app.get("/dashboard/users/activate/{user_id}")
async def activate_user(request: Request, user_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET is_active = 1, is_blocked = 0, frozen_until = NULL WHERE id = ? AND role != 'admin'",
                (user_id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при активации пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

@app.get("/dashboard/users/deactivate/{user_id}")
async def deactivate_user(request: Request, user_id: int):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET is_active = 0 WHERE id = ? AND role != 'admin'",
                (user_id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при деактивации пользователя: {e}")
    
    return RedirectResponse(url="/dashboard/users", status_code=303)

# --- МОДУЛИ ---
@app.get("/dashboard/modules", response_class=HTMLResponse)
async def modules(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse("modules.html", {"request": request, "active": "modules"})

# --- СТАТИСТИКА ---
@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, title, data, filename, scan_count, last_scan
                FROM qr_codes
                ORDER BY id DESC
            """)
            stats_list = await cursor.fetchall()
        return templates.TemplateResponse("stats.html", {
            "request": request,
            "active": "stats",
            "stats_list": stats_list
        })
    except Exception as e:
        logger.error(f"Ошибка при загрузке статистики: {e}")
        return templates.TemplateResponse("stats.html", {
            "request": request,
            "active": "stats",
            "stats_list": [],
            "error": "Ошибка при загрузке статистики"
        })

# --- НАСТРОЙКИ ---
@app.get("/dashboard/settings", response_class=HTMLResponse)
async def settings(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})

# --- ВСЕ УСЛУГИ ---
@app.get("/dashboard/services", response_class=HTMLResponse)
async def all_services(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse("services.html", {"request": request})

@app.get("/dashboard/business", response_class=HTMLResponse)
async def business_module(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse("business.html", {"request": request})

@app.get("/dashboard/cleaning", response_class=HTMLResponse)
async def cleaning_services(request: Request):
    user = await check_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse("cleaning.html", {"request": request})

# --- МАРШРУТЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ (только просмотр) ---
@app.get("/user/modules", response_class=HTMLResponse)
async def user_modules(request: Request):
    user_resp = await check_user(request)
    if not isinstance(user_resp, tuple):
        return user_resp
    return templates.TemplateResponse("user_modules.html", {"request": request})

@app.get("/user/services", response_class=HTMLResponse)
async def user_services(request: Request):
    user_resp = await check_user(request)
    if not isinstance(user_resp, tuple):
        return user_resp
    return templates.TemplateResponse("services.html", {"request": request})

@app.get("/user/business", response_class=HTMLResponse)
async def user_business(request: Request):
    user_resp = await check_user(request)
    if not isinstance(user_resp, tuple):
        return user_resp
    return templates.TemplateResponse("business.html", {"request": request})

@app.get("/user/cleaning", response_class=HTMLResponse)
async def user_cleaning(request: Request):
    user_resp = await check_user(request)
    if not isinstance(user_resp, tuple):
        return user_resp
    return templates.TemplateResponse("cleaning.html", {"request": request})

# --- СТРАНИЦА БЛОКИРОВКИ ---
@app.get("/user/blocked", response_class=HTMLResponse)
async def user_blocked(request: Request):
    return templates.TemplateResponse("user_blocked.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
