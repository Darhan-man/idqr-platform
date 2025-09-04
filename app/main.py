from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import qrcode
import os
import uuid
from datetime import datetime
import aiosqlite
from PIL import Image, ImageDraw, ImageFont
from functools import wraps

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

QR_FOLDER = "static/qr"
FONT_PATH = "fonts/RobotoSlab-Bold.ttf"
DB_PATH = "qr_data.db"
ADMIN_CODE = "1990"

os.makedirs(QR_FOLDER, exist_ok=True)

# Сессии
app.add_middleware(SessionMiddleware, secret_key="f7d9b6a2c3e14f89d5b0a7c6e2f38d9b1c7f0a5d4e8b3c2a9f6d1e0c5b7a3f2d")

# --- Инициализация БД ---
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS qr_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                data TEXT,
                filename TEXT,
                module TEXT,
                created_at TEXT
            )
        """)
        await db.commit()

# --- Главная и логин ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == ADMIN_CODE:
        request.session["is_admin"] = True
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "error": "Неверный код"})

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

# --- Декораторы ---
def admin_only(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not request.session.get("is_admin"):
            return RedirectResponse(url="/")
        return await func(request, *args, **kwargs)
    return wrapper

def user_only(module_name: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            if request.session.get("user_module") != module_name:
                return RedirectResponse(url="/")
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator

# --- Панель QR-кодов ---
@app.get("/dashboard/qr", response_class=HTMLResponse)
@admin_only
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

@app.post("/generate_qr", response_class=HTMLResponse)
@admin_only
async def generate_qr(request: Request, qrdata: str = Form(...), title: str = Form(...), module: str = Form(...)):
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
            "INSERT INTO qr_codes (title, data, filename, module, created_at) VALUES (?, ?, ?, ?, ?)",
            (title, qrdata, filename, module, now)
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

@app.get("/delete_qr/{qr_id}")
@admin_only
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

# --- Сканирование QR ---
@app.get("/scan/{qr_id}")
async def scan_qr(request: Request, qr_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT module FROM qr_codes WHERE id = ?", (qr_id,))
        row = await cursor.fetchone()
        if not row:
            return RedirectResponse(url="/")
        module = row[0]
        # Сохраняем модуль в сессии пользователя
        request.session["user_module"] = module
    return RedirectResponse(url=f"/dashboard/{module}", status_code=303)

# --- Пользовательские модули ---
@app.get("/dashboard/services", response_class=HTMLResponse)
@user_only("services")
async def services(request: Request):
    return templates.TemplateResponse("services.html", {"request": request})

@app.get("/dashboard/business", response_class=HTMLResponse)
@user_only("business")
async def business(request: Request):
    return templates.TemplateResponse("business.html", {"request": request})

@app.get("/dashboard/cleaning", response_class=HTMLResponse)
@user_only("cleaning")
async def cleaning(request: Request):
    return templates.TemplateResponse("cleaning.html", {"request": request})

# --- Остальные страницы админки ---
@app.get("/dashboard/modules", response_class=HTMLResponse)
@admin_only
async def modules(request: Request):
    return templates.TemplateResponse("modules.html", {"request": request, "active": "modules"})

@app.get("/dashboard/users", response_class=HTMLResponse)
@admin_only
async def users(request: Request):
    return templates.TemplateResponse("users.html", {"request": request, "active": "users"})

@app.get("/dashboard/stats", response_class=HTMLResponse)
@admin_only
async def stats(request: Request):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
        qr_list = await cursor.fetchall()
    return templates.TemplateResponse("stats.html", {"request": request, "qr_list": qr_list, "active": "stats"})

@app.get("/dashboard/settings", response_class=HTMLResponse)
@admin_only
async def settings(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})
