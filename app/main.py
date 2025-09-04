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

QR_FOLDER = "static/qr"
FONT_PATH = "fonts/RobotoSlab-Bold.ttf"
DB_PATH = "qr_data.db"
ADMIN_CODE = "1990"

os.makedirs(QR_FOLDER, exist_ok=True)

@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS qr_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                data TEXT,
                filename TEXT,
                created_at TEXT
            )
        """)
        await db.commit()

# Главная (вход)
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# Логин
@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == ADMIN_CODE:
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "error": "Неверный код"})

# Панель QR-кодов
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

# Генерация QR
@app.post("/generate_qr", response_class=HTMLResponse)
async def generate_qr(request: Request, qrdata: str = Form(...), title: str = Form(...)):
    filename = f"{uuid.uuid4()}.png"
    filepath = os.path.join(QR_FOLDER, filename)

    qr_img = qrcode.make(qrdata).convert("RGB")

    try:
        font = ImageFont.truetype(FONT_PATH, 32)
    except IOError:
        font = ImageFont.load_default()

    draw_temp = ImageDraw.Draw(qr_img)
    text_width, text_height = draw_temp.textbbox((0, 0), title, font=font)[2:]
    new_width = max(qr_img.width, text_width + 40)
    new_height = qr_img.height + text_height + 30

    final_img = Image.new("RGB", (new_width, new_height), "white")
    draw = ImageDraw.Draw(final_img)

    text_x = (new_width - text_width) // 2
    draw.text((text_x, 10), title, font=font, fill="red")

    qr_x = (new_width - qr_img.width) // 2
    final_img.paste(qr_img, (qr_x, text_height + 20))

    final_img.save(filepath)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO qr_codes (title, data, filename, created_at) VALUES (?, ?, ?, ?)",
            (title, qrdata, filename, now)
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
        qr_list = await cursor.fetchall()

    qr_url = f"/static/qr/{filename}"
    return templates.TemplateResponse("qr.html", {
        "request": request,
        "qr_url": qr_url,
        "qr_title": title,
        "qr_list": qr_list,
        "active": "qr"
    })

# Удаление QR
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

# 🧩 Модули
@app.get("/dashboard/modules", response_class=HTMLResponse)
async def modules(request: Request):
    return templates.TemplateResponse("modules.html", {"request": request, "active": "modules"})

# 👤 Пользователи
@app.get("/dashboard/users", response_class=HTMLResponse)
async def users(request: Request):
    return templates.TemplateResponse("users.html", {"request": request, "active": "users"})

# 📊 Статистика
@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats(request: Request):
    return templates.TemplateResponse("stats.html", {"request": request, "active": "stats"})

# ⚙️ Настройки
@app.get("/dashboard/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})

# 🧾 ВСЕ УСЛУГИ
@app.get("/dashboard/services", response_class=HTMLResponse)
async def all_services(request: Request):
    return templates.TemplateResponse("services.html", {"request": request})

@app.get("/dashboard/business", response_class=HTMLResponse)
async def business_module(request: Request):
    return templates.TemplateResponse("business.html", {"request": request})

# Уборка и гигиена
@app.get("/dashboard/cleaning", response_class=HTMLResponse)
async def cleaning_services(request: Request):
    return templates.TemplateResponse("cleaning.html", {"request": request}
