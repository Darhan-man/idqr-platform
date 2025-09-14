from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import qrcode
import os
import uuid
from datetime import datetime
import aiosqlite
from PIL import Image, ImageDraw, ImageFont
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Константы ---
QR_FOLDER = "static/qr"
DB_PATH = "qr_data.db"
ADMIN_CODE = "admin1990"
BASE_URL = "https://idqr-platform.onrender.com"  # Замените на ваш URL

# Создаем папки если они не существуют
os.makedirs(QR_FOLDER, exist_ok=True)
os.makedirs("static/fonts", exist_ok=True)

# --- ИНИЦИАЛИЗАЦИЯ БД ---
@app.on_event("startup")
async def startup():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS qr_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    data TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    scan_count INTEGER DEFAULT 0,
                    last_scan TEXT
                )
            """)
            await db.commit()
            logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка при инициализации БД: {e}")

# --- ГЛАВНАЯ ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- ЛОГИН ---
@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == ADMIN_CODE:
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "error": "Неверный код"})

# --- ПАНЕЛЬ QR ---
@app.get("/dashboard/qr", response_class=HTMLResponse)
async def dashboard_qr(request: Request):
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
async def generate_qr(request: Request, qrdata: str = Form(...), title: str = Form(...)):
    try:
        # Генерируем уникальное имя файла
        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(QR_FOLDER, filename)

        # Сначала создаем запись в БД
        async with aiosqlite.connect(DB_PATH) as db:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor = await db.execute(
                "INSERT INTO qr_codes (title, data, filename, created_at) VALUES (?, ?, ?, ?)",
                (title, qrdata, filename, now)
            )
            await db.commit()
            qr_id = cursor.lastrowid
            logger.info(f"Создана запись в БД с ID: {qr_id}")

        # Генерируем QR-код
        scan_url = f"{BASE_URL}/scan/{qr_id}"
        qr_img = qrcode.make(scan_url).convert("RGB")
        
        # Добавляем текст поверх QR-кода
        try:
            # Пытаемся использовать шрифт RobotoSlab
            font = ImageFont.truetype("static/fonts/RobotoSlab-Bold.ttf", 28)
        except IOError:
            # Если шрифт не найден, используем стандартный
            font = ImageFont.load_default()
            logger.warning("Шрифт RobotoSlab-Bold.ttf не найден, используется стандартный шрифт")
        
        # Создаем изображение с текстом
        draw = ImageDraw.Draw(qr_img)
        text_bbox = draw.textbbox((0, 0), title, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        
        # Создаем новое изображение с местом для текста
        new_img = Image.new("RGB", (qr_img.width, qr_img.height + text_height + 20), "white")
        new_img.paste(qr_img, (0, text_height + 20))
        
        # Рисуем текст
        draw = ImageDraw.Draw(new_img)
        text_x = (new_img.width - text_width) // 2
        draw.text((text_x, 10), title, font=font, fill="black")
        
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
async def delete_qr(qr_id: int):
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

# --- МОДУЛИ ---
@app.get("/dashboard/modules", response_class=HTMLResponse)
async def modules(request: Request):
    return templates.TemplateResponse("modules.html", {"request": request, "active": "modules"})

# --- ПОЛЬЗОВАТЕЛИ ---
@app.get("/dashboard/users", response_class=HTMLResponse)
async def users(request: Request):
    return templates.TemplateResponse("users.html", {"request": request, "active": "users"})

# --- СТАТИСТИКА ---
@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats(request: Request):
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
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})

# --- ВСЕ УСЛУГИ ---
@app.get("/dashboard/services", response_class=HTMLResponse)
async def all_services(request: Request):
    return templates.TemplateResponse("services.html", {"request": request})

@app.get("/dashboard/business", response_class=HTMLResponse)
async def business_module(request: Request):
    return templates.TemplateResponse("business.html", {"request": request})

@app.get("/dashboard/cleaning", response_class=HTMLResponse)
async def cleaning_services(request: Request):
    return templates.TemplateResponse("cleaning.html", {"request": request})