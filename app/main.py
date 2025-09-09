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

    # --- Новая, более надежная функция для текста над QR ---
    def draw_title_above_qr_dynamic(qr_img, title, font_path=FONT_PATH):
    qr_width, qr_height = qr_img.size

    if not title.strip():
        return qr_img

    font_size = 35
    max_text_width = qr_width - 40
    draw_temp = ImageDraw.Draw(Image.new('RGB', (1, 1)))

    while font_size > 10:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            font = ImageFont.load_default()
            break

        words = title.split()
        lines = []
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = draw_temp.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] > max_text_width:
                if current_line:
                    lines.append(current_line)
                current_line = word
            else:
                current_line = test_line
        if current_line:
            lines.append(current_line)

        full_bbox = draw_temp.multiline_textbbox((0, 0), '\n'.join(lines), font=font)
        total_text_height = full_bbox[3] - full_bbox[1]

        if total_text_height + 60 + qr_height <= 
        1200:
            break
        font_size -= 2

    if font is None:
        font = ImageFont.load_default()

    top_padding = 30
    bottom_padding = 30
    line_spacing = 10
    final_height = qr_height + total_text_height + top_padding + bottom_padding
    final_img = Image.new("RGB", (qr_width, final_height), "white")
    draw_final = ImageDraw.Draw(final_img)

    y = top_padding
    for line in lines:
        line_bbox = draw_final.textbbox((0, 0), line, font=font)
        x = (qr_width - (line_bbox[2] - line_bbox[0])) // 2

        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx != 0 or dy != 0:
                    draw_final.text((x + dx, y + dy), line, font=font, fill="black")
        draw_final.text((x, y), line, font=font, fill="red")
        y += (line_bbox[3] - line_bbox[1]) + line_spacing

    qr_x = (qr_width - qr_img.size[0]) // 2
    qr_y = y + bottom_padding
    final_img.paste(qr_img, (qr_x, int(qr_y)))

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
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=5,
        border=1,
    )
    qr.add_data(scan_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    
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
    