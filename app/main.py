from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import qrcode
import os
import uuid
from datetime import datetime
import aiosqlite
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Константы ---
QR_FOLDER = "static/qr"
FONT_PATH = "fonts/RobotoSlab-Bold.ttf"
DB_PATH = "qr_data.db"
ADMIN_CODE = "1990"
BASE_URL = "https://idqr-platform.onrender.com"  # твой полный URL на Render

os.makedirs(QR_FOLDER, exist_ok=True)

# --- ИНИЦИАЛИЗАЦИЯ БД ---
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS qr_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                data TEXT,
                filename TEXT,
                created_at TEXT,
                scan_count INTEGER DEFAULT 0,
                last_scan TEXT
            )
        """)
        await db.commit()

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

# --- ПАНЕЛЬ QR (только для админа) ---
@app.get("/dashboard/qr", response_class=HTMLResponse)
async def dashboard_qr(request: Request):
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

# --- Генерация QR ---
@app.get("/generate_qr")
async def generate_qr_redirect():
    return RedirectResponse(url="/dashboard/qr")

@app.post("/generate_qr", response_class=HTMLResponse)
async def generate_qr(
    request: Request,
    qrdata: str = Form(...),  # <- сюда теперь записывай ССЫЛКУ на модуль (например, /dashboard/cleaning)
    title: str = Form(...),
    text_y: int = Form(10)
):
    filename = f"{uuid.uuid4()}.png"
    filepath = os.path.join(QR_FOLDER, filename)

    # --- Сохраняем запись в БД ---
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await db.execute(
            "INSERT INTO qr_codes (title, data, filename, created_at) VALUES (?, ?, ?, ?)",
            (title, qrdata, filename, now)
        )
        await db.commit()
        qr_id = cursor.lastrowid

    # --- Генерация QR с маршрутом /scan/{id} ---
    scan_url = f"{BASE_URL}/scan/{qr_id}"
    qr_img = qrcode.make(scan_url).convert("RGB")

    # --- Текст под QR ---
    FONT_PATH = "static/fonts/RobotoSlab-Bold.ttf"
    FONT_SIZE = 32
    TEXT_COLOR = "red"
    BETWEEN_MARGIN = 15
    MIN_WIDTH = 150
    MIN_HEIGHT = 150

    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except IOError:
        font = ImageFont.load_default()

    draw_temp = ImageDraw.Draw(qr_img)
    bbox = draw_temp.textbbox((0, 0), title, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    new_width = max(qr_img.width, text_width, MIN_WIDTH) + 20
    new_height = text_y + text_height + BETWEEN_MARGIN + qr_img.height + 10
    if new_height < MIN_HEIGHT:
        new_height = MIN_HEIGHT

    final_img = Image.new("RGB", (new_width, new_height), "white")
    draw = ImageDraw.Draw(final_img)

    text_x = (new_width - text_width) // 2
    draw.text((text_x, text_y), title, font=font, fill=TEXT_COLOR)

    qr_x = (new_width - qr_img.width) // 2
    qr_y = text_y + text_height + BETWEEN_MARGIN
    final_img.paste(qr_img, (qr_x, qr_y))

    final_img.save(filepath)
    qr_url = f"/static/qr/{filename}"

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
        qr_list = await cursor.fetchall()

    return templates.TemplateResponse("qr.html", {
        "request": request,
        "qr_url": qr_url,
        "qr_title": title,
        "qr_list": qr_list,
        "active": "qr"
    })

# --- СКАНИРОВАНИЕ QR ---
@app.get("/scan/{qr_id}")
async def scan_qr(qr_id: int):
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
            # ВАЖНО: теперь редирект только на сохранённую ссылку (например /dashboard/cleaning)
            return RedirectResponse(data)
    return RedirectResponse("/", status_code=303)

# --- УДАЛЕНИЕ QR ---
@app.get("/delete_qr/{qr_id}")
async def delete_qr(qr_id: int):
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

# --- МОДУЛИ ---
@app.get("/dashboard/modules", response_class=HTMLResponse)
async def modules(request: Request):
    return templates.TemplateResponse("modules.html", {"request": request, "active": "modules"})

@app.get("/dashboard/business", response_class=HTMLResponse)
async def business_module(request: Request):
    return templates.TemplateResponse("business.html", {"request": request})

@app.get("/dashboard/cleaning", response_class=HTMLResponse)
async def cleaning_services(request: Request):
    return templates.TemplateResponse("cleaning.html", {"request": request})

# --- ТОЛЬКО для админа ---
@app.get("/dashboard/users", response_class=HTMLResponse)
async def users(request: Request):
    return templates.TemplateResponse("users.html", {"request": request, "active": "users"})

@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats(request: Request):
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

@app.get("/dashboard/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})

@app.get("/dashboard/services", response_class=HTMLResponse)
async def all_services(request: Request):
    return templates.TemplateResponse("services.html", {"request": request})
