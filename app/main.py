from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import qrcode
import os
import uuid
from datetime import datetime
import aiosqlite
import secrets

# --- Инициализация приложения ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Сессии ---
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

# --- Middleware для защиты страниц ---
@app.middleware("http")
async def restrict_middleware(request: Request, call_next):
    path = request.url.path

    # публичные страницы
    if path.startswith("/static") or path.startswith("/scan") or path in ["/", "/login", "/favicon.ico", "/robots.txt"]:
        return await call_next(request)

    # доступ для админа
    if request.session.get("is_admin"):
        return await call_next(request)

    # доступ после сканирования QR
    allowed = request.session.get("allowed_page")
    if allowed and path.startswith(allowed):
        return await call_next(request)

    # иначе редирект на главную
    return RedirectResponse("/", status_code=303)

# --- Папка QR ---
QR_FOLDER = os.path.join("static", "qr")
os.makedirs(QR_FOLDER, exist_ok=True)

# --- База данных ---
DB_PATH = "qr_data.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS qr_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                url TEXT,
                filename TEXT,
                created_at TEXT,
                scan_count INTEGER DEFAULT 0,
                last_scan TEXT
            )
        """)
        await db.commit()

@app.on_event("startup")
async def startup():
    await init_db()

# --- Главная и robots.txt ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/robots.txt")
async def robots():
    robots_path = os.path.join("static", "robots.txt")
    if os.path.exists(robots_path):
        return FileResponse(robots_path)
    return PlainTextResponse("User-agent: *\nDisallow:", status_code=200)

# --- Логин админа ---
ADMIN_CODE = "1990"

@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == ADMIN_CODE:
        request.session["is_admin"] = True
        return RedirectResponse("/dashboard/qr", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "error": "Неверный код"})

# --- Панель QR ---
@app.get("/dashboard/qr", response_class=HTMLResponse)
async def dashboard_qr(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, title, url, filename, scan_count, last_scan FROM qr_codes ORDER BY id DESC")
        qr_list = await cursor.fetchall()

    return templates.TemplateResponse("qr.html", {"request": request, "qr_list": qr_list, "active": "qr"})

# --- Генерация QR ---
@app.post("/generate")
async def generate_qr(title: str = Form(...), url: str = Form(...)):
    filename = f"{uuid.uuid4()}.png"
    filepath = os.path.join(QR_FOLDER, filename)

    img = qrcode.make(url)
    img.save(filepath)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO qr_codes (title, url, filename, created_at) VALUES (?, ?, ?, ?)",
            (title, url, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()

    return RedirectResponse("/dashboard/qr", status_code=303)

# --- Сканирование QR ---
@app.get("/scan/{qr_id}")
async def scan_qr(request: Request, qr_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT url FROM qr_codes WHERE id = ?", (qr_id,))
        row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="QR not found")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE qr_codes SET scan_count = scan_count + 1, last_scan = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), qr_id)
        )
        await db.commit()

    request.session["allowed_page"] = "/dashboard/qr"

    return RedirectResponse(row[0], status_code=302)

# --- Статистика ---
@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, title, url, filename, scan_count, last_scan FROM qr_codes ORDER BY id DESC")
        stats_list = await cursor.fetchall()

    return templates.TemplateResponse("stats.html", {"request": request, "stats_list": stats_list, "active": "stats"})

# --- Удаление QR ---
@app.get("/delete_qr/{qr_id}")
async def delete_qr(request: Request, qr_id: int):
    if not request.session.get("is_admin"):
        return RedirectResponse("/", status_code=303)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT filename FROM qr_codes WHERE id = ?", (qr_id,))
        row = await cursor.fetchone()
        if row:
            file_path = os.path.join(QR_FOLDER, row[0])
            if os.path.exists(file_path):
                os.remove(file_path)
            await db.execute("DELETE FROM qr_codes WHERE id = ?", (qr_id,))
            await db.commit()

    return RedirectResponse("/dashboard/qr", status_code=303)
