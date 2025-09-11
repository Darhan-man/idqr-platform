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
FONT_PATH = "static/fonts/RobotoSlab-Bold.ttf"
DB_PATH = "qr_data.db"
ADMIN_CODE = "1990"
BASE_URL = "https://idqr-platform.onrender.com"

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

# --- ПАНЕЛЬ QR ---
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

# --- ПРОСМОТР ОТДЕЛЬНОГО QR ---
@app.get("/dashboard/qr/view/{qr_id}", response_class=HTMLResponse)
async def view_qr(request: Request, qr_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
        qr_list = await cursor.fetchall()
        cursor = await db.execute("SELECT * FROM qr_codes WHERE id = ?", (qr_id,))
        row = await cursor.fetchone()
    if row:
        qr_url = f"/static/qr/{row[3]}"
        qr_title = row[1]
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

# --- Генерация QR (ФОРМА, GET) ---
@app.get("/generate_qr", response_class=HTMLResponse)
async def generate_qr_form(request: Request):
    return templates.TemplateResponse("new_qr.html", {"request": request, "active": "qr"})

# --- Генерация QR (СОЗДАНИЕ, POST) ---
@app.post("/generate_qr")
async def generate_qr(request: Request, qrdata: str = Form(...), title: str = Form(...)):
    # Генерируем уникальное имя файла
    filename = f"{uuid.uuid4()}.png"
    filepath = os.path.join(QR_FOLDER, filename)

    # --- Функция для текста над QR ---
    def draw_title_above_qr_dynamic(qr_img, title, font_path=FONT_PATH):
        qr_width, qr_height = qr_img.size
        font_size = 32
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            font = ImageFont.load_default()

        draw = ImageDraw.Draw(qr_img)
        max_text_width = qr_width - 10

        # Автоуменьшение шрифта
        while True:
            bbox = draw.textbbox((0, 0), title, font=font)
            if bbox[2] - bbox[0] <= max_text_width or font_size <= 14:
                break
            font_size -= 2
            font = ImageFont.truetype(font_path, font_size)

        # Разбиваем на строки
        words = title.split()
        lines = []
        line = ""
        for word in words:
            test_line = f"{line} {word}".strip()
            bbox = draw.textbbox((0,0), test_line, font=font)
            if bbox[2] > max_text_width:
                if line:
                    lines.append(line)
                line = word
            else:
                line = test_line
        lines.append(line)

        # Высота и ширина итогового изображения
        text_height_total = sum([draw.textbbox((0,0), l, font=font)[3] - draw.textbbox((0,0), l, font=font)[1] + 5 for l in lines])
        final_width = max(qr_width, max([draw.textbbox((0,0), l, font=font)[2] - draw.textbbox((0,0), l, font=font)[0] for l in lines]) + 20)
        final_height = qr_height + text_height_total

        # Создаём финальное изображение
        final_img = Image.new("RGB", (final_width, final_height), "white")
        draw_final = ImageDraw.Draw(final_img)

        # Рисуем текст с обводкой
        y = 5
        for line in lines:
            bbox = draw_final.textbbox((0,0), line, font=font)
            text_width = bbox[2] - bbox[0]
            x = (final_width - text_width) // 2
            for dx in [-1,0,1]:
                for dy in [-1,0,1]:
                    if dx != 0 or dy != 0:
                        draw_final.text((x+dx, y+dy), line, font=font, fill="black")
            draw_final.text((x, y), line, font=font, fill="red")
            y += bbox[3] - bbox[1] + 5

        # Вставляем QR-код под текст
        qr_x = (final_width - qr_width) // 2
        final_img.paste(qr_img, (qr_x, text_height_total + 10))
        return final_img

    # --- Создаём запись в БД ---
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await db.execute(
            "INSERT INTO qr_codes (title, data, filename, created_at) VALUES (?, ?, ?, ?)",
            (title, qrdata, filename, now)
        )
        await db.commit()
        qr_id = cursor.lastrowid

    # --- Генерация QR с правильной ссылкой ---
    scan_url = f"{BASE_URL}/scan/{qr_id}"
    qr_img = qrcode.make(scan_url).convert("RGB")
    final_img = draw_title_above_qr_dynamic(qr_img, title)
    final_img.save(filepath)

    # --- Redirect чтобы избежать дублирования при обновлении страницы ---
    return RedirectResponse(url=f"/dashboard/qr/view/{qr_id}", status_code=303)


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

# --- ПОЛЬЗОВАТЕЛИ ---
@app.get("/dashboard/users", response_class=HTMLResponse)
async def users(request: Request):
    return templates.TemplateResponse("users.html", {"request": request, "active": "users"})

# --- СТАТИСТИКА ---
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
