from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import qrcode
import os
import uuid
from datetime import datetime
import aiosqlite
from PIL import Image, ImageDraw, ImageFont
import secrets

# --- Инициализация приложения ---
app = FastAPI()

# ✅ Постоянный секрет (лучше задать через переменные окружения на Render)
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

# --- Ограничивающее middleware ---
class RestrictMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Публичные пути
        if (
            path.startswith("/static")
            or path.startswith("/scan")
            or path in ["/", "/login", "/favicon.ico", "/robots.txt"]
        ):
            return await call_next(request)

        # Админ — полный доступ
        if request.session.get("is_admin"):
            return await call_next(request)

        # Пользователь после скана — доступ только к разрешённому пути
        allowed = request.session.get("allowed_page")
        if allowed and path.startswith(allowed):
            return await call_next(request)

        # Всё остальное — редирект на главную
        return RedirectResponse("/", status_code=303)

# ⚠️ Подключаем ограничитель ПОСЛЕ SessionMiddleware
app.add_middleware(RestrictMiddleware)

# --- Настройка статики и шаблонов ---
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Константы ---
QR_FOLDER = os.path.join("static", "qr")
FONT_PATH = os.path.join("static", "fonts", "RobotoSlab-Bold.ttf")
DB_PATH = "qr_data.db"
ADMIN_CODE = "1990"
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

os.makedirs(QR_FOLDER, exist_ok=True)

# --- База данных ---
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

        # Добавляем недостающие колонки
        cursor = await db.execute("PRAGMA table_info(qr_codes)")
        cols = await cursor.fetchall()
        colnames = [c[1] for c in cols]
        if "scan_count" not in colnames:
            await db.execute("ALTER TABLE qr_codes ADD COLUMN scan_count INTEGER DEFAULT 0")
        if "last_scan" not in colnames:
            await db.execute("ALTER TABLE qr_codes ADD COLUMN last_scan TEXT")
        await db.commit()

# --- Главная страница и вход ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == ADMIN_CODE:
        request.session["is_admin"] = True
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "error": "Неверный код"})

# --- Админ-панель (QR список) ---
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
async def generate_qr(request: Request, qrdata: str = Form(...), title: str = Form(...), text_y: int = Form(10)):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)

    if not isinstance(qrdata, str) or not qrdata.startswith("/dashboard"):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
            qr_list = await cursor.fetchall()
        return templates.TemplateResponse("qr.html", {
            "request": request,
            "qr_list": qr_list,
            "error": "QR data должен начинаться с /dashboard/ (для безопасности)",
            "active": "qr"
        })

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

    scan_url = f"{BASE_URL}/scan/{qr_id}"
    qr_img = qrcode.make(scan_url).convert("RGB")

    FONT_SIZE = 28
    TEXT_COLOR = "black"
    BETWEEN_MARGIN = 12
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except Exception:
        font = ImageFont.load_default()

    draw_temp = ImageDraw.Draw(qr_img)
    bbox = draw_temp.textbbox((0, 0), title, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    padding = 10
    new_width = max(qr_img.width, text_width) + padding * 2
    new_height = padding + text_height + BETWEEN_MARGIN + qr_img.height + padding

    final_img = Image.new("RGB", (new_width, new_height), "white")
    draw = ImageDraw.Draw(final_img)

    text_x = (new_width - text_width) // 2
    text_y = padding
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

# --- Сканирование QR ---
@app.get("/scan/{qr_id}")
async def scan_qr(qr_id: int, request: Request):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT data, scan_count FROM qr_codes WHERE id = ?", (qr_id,))
        row = await cursor.fetchone()
        if row:
            data, scan_count = row
            await db.execute(
                "UPDATE qr_codes SET scan_count = ?, last_scan = ? WHERE id = ?",
                (scan_count + 1 if scan_count else 1, datetime.now().isoformat(), qr_id)
            )
            await db.commit()

            # 🎯 После скана разрешаем только этот раздел
            request.session["allowed_page"] = data
            return RedirectResponse(data)
    return RedirectResponse("/", status_code=303)

# --- Остальные страницы ---
@app.get("/dashboard/modules", response_class=HTMLResponse)
async def modules(request: Request):
    return templates.TemplateResponse("modules.html", {"request": request, "active": "modules"})

@app.get("/dashboard/business", response_class=HTMLResponse)
async def business_module(request: Request):
    return templates.TemplateResponse("business.html", {"request": request})

@app.get("/dashboard/cleaning", response_class=HTMLResponse)
async def cleaning_services(request: Request):
    return templates.TemplateResponse("cleaning.html", {"request": request})

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
    return templates.TemplateResponse("services.html", {"request": request})
