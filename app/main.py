
from fastapi import FastAPI, Form, Request, Depends
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

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecret")  # ключ для сессии
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

# --- Middleware: защита админки ---
def admin_required(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)

# --- ГЛАВНАЯ ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- ЛОГИН ---
@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == ADMIN_CODE:
        request.session["is_admin"] = True
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "error": "Неверный код"})

# --- ПАНЕЛЬ QR (только админ) ---
@app.get("/dashboard/qr", response_class=HTMLResponse)
async def dashboard_qr(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)

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
    qrdata: str = Form(...),
    title: str = Form(...),
    text_y: int = Form(10)
):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)

    filename = f"{uuid.uuid4()}.png"
    filepath = os.path.join(QR_FOLDER, filename)

    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await db.execute(
            "INSERT INTO qr_codes (title, data, filename, created_at) VALUES (?, ?, ?, ?)",
            (title, qrdata, filename, now)
        )
        await db.commit()
        qr_id = cursor.lastrowid

    # --- QR с маршрутом /scan/{id} ---
    scan_url = f"{BASE_URL}/scan/{qr_id}"
    qr_img = qrcode.make(scan_url).convert("RGB")

    try:
        font = ImageFont.truetype(FONT_PATH, 32)
    except IOError:
        font = ImageFont.load_default()

    # текст + QR
    final_img = Image.new("RGB", (qr_img.width, qr_img.height + 60), "white")
    draw = ImageDraw.Draw(final_img)
    text_width = draw.textlength(title, font=font)
    text_x = (qr_img.width - text_width) // 2
    draw.text((text_x, 10), title, font=font, fill="red")
    final_img.paste(qr_img, (0, 50))

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
async def scan_qr(qr_id: int, request: Request):
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

            # сохранить "разрешённый маршрут" в сессии
            request.session["allowed_page"] = data
            return RedirectResponse(data)
    return RedirectResponse("/", status_code=303)

# --- Ограничение доступа к страницам модулей ---
@app.middleware("http")
async def restrict_pages(request: Request, call_next):
    path = request.url.path
    # если админ — полный доступ
    if request.session.get("is_admin"):
        return await call_next(request)

    # если пользователь со сканом — доступ только к "allowed_page"
    allowed = request.session.get("allowed_page")
    if allowed and path.startswith(allowed):
        return await call_next(request)

    # всё остальное запрещено
    if path.startswith("/dashboard"):
        return RedirectResponse("/", status_code=303)

    return await call_next(request)

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

# --- ТОЛЬКО админ ---
@app.get("/dashboard/users", response_class=HTMLResponse)
async def users(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("users.html", {"request": request, "active": "users"})

@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)
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
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})

@app.get("/dashboard/services", response_class=HTMLResponse)
async def all_services(request: Request):
    return templates.TemplateResponse("servi
