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

# --- Генерация QR ---
@app.post("/generate_qr", response_class=HTMLResponse)
async def generate_qr(
    request: Request,
    qrdata: str = Form(...),
    title: str = Form(...),
    text_color: str = Form("#FF0000")  # цвет текста по умолчанию
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

    # --- Генерация QR ---
    scan_url = f"{BASE_URL}/scan/{qr_id}"
    qr_img = qrcode.make(scan_url).convert("RGB")

    # --- Функция для рисования текста с переносом и автоуменьшением шрифта ---
    def draw_qr_with_text(qr_img, title, font_path=FONT_PATH, text_color=text_color, max_width=None, max_font_size=32, min_font_size=12):
        # Определяем макс ширину
        if max_width is None:
            max_width = qr_img.width - 20

        # Подбор шрифта с уменьшением размера, если текст слишком длинный
        font_size = max_font_size
        while font_size >= min_font_size:
            try:
                font = ImageFont.truetype(font_path, font_size)
            except IOError:
                font = ImageFont.load_default()
            # Проверяем, можно ли поместить текст в ширину QR
            draw_temp = ImageDraw.Draw(qr_img)
            words = title.split()
            lines = []
            line = ""
            fits = True
            for word in words:
                test_line = f"{line} {word}".strip()
                bbox = draw_temp.textbbox((0, 0), test_line, font=font)
                w = bbox[2] - bbox[0]
                if w <= max_width:
                    line = test_line
                else:
                    if line:
                        lines.append(line)
                    line = word
            if line:
                lines.append(line)

            # Если строк слишком много и ширина всё ещё не помещается — уменьшаем шрифт
            too_wide = any(draw_temp.textbbox((0,0), l, font=font)[2] - draw_temp.textbbox((0,0), l, font=font)[0] > max_width for l in lines)
            if not too_wide:
                break
            font_size -= 2

        # Вычисляем размеры холста
        line_heights = []
        line_widths = []
        for l in lines:
            bbox = draw_temp.textbbox((0, 0), l, font=font)
            line_widths.append(bbox[2] - bbox[0])
            line_heights.append(bbox[3] - bbox[1])
        text_height_total = sum(line_heights) + 5 * (len(lines) - 1)
        new_width = max(qr_img.width, max(line_widths) + 20)
        new_height = qr_img.height + text_height_total + 20

        final_img = Image.new("RGB", (new_width, new_height), "white")
        draw = ImageDraw.Draw(final_img)

        # Рисуем текст сверху
        current_y = 10
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            draw.text(((new_width - w)//2, current_y), line, font=font, fill=text_color)
            current_y += h + 5

        # Вставляем QR-код
        qr_x = (new_width - qr_img.width) // 2
        final_img.paste(qr_img, (qr_x, current_y + 5))

        return final_img

    # --- Генерация финального изображения ---
    final_img = draw_qr_with_text(qr_img, title, text_color=text_color)
    final_img.save(filepath)
    qr_url = f"/static/qr/{filename}"

    # --- Обновляем список QR ---
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
