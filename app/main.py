from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import qrcode
import os
import uuid
import secrets
from datetime import datetime
import aiosqlite
from PIL import Image, ImageDraw, ImageFont

# --- Инициализация приложения ---
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Настройки сессий ---
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)

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

        # Доступ для админа
        if request.session.get("is_admin"):
            return await call_next(request)

        # Доступ после сканирования QR — только в разрешённый раздел
        allowed = request.session.get("allowed_page")
        if allowed and path.startswith(allowed):
            return await call_next(request)

        # Иначе — редирект на главную
        return RedirectResponse("/", status_code=303)

# ⚠️ Порядок имеет значение: RestrictMiddleware должен быть первым
app.add_middleware(RestrictMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

# --- Инициализация БД ---
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

# --- Генерация QR-кода ---
@app.post("/generate")
async def generate_qr(title: str = Form(...), url: str = Form(...)):
    filename = f"{uuid.uuid4()}.png"
    filepath = os.path.join("static", "qr", filename)

    # создаём директорию если её нет
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # генерируем QR
    img = qrcode.make(url)
    img.save(filepath)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO qr_codes (title, url, filename, created_at) VALUES (?, ?, ?, ?)",
            (title, url, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
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

    # фиксируем скан
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE qr_codes SET scan_count = scan_count + 1, last_scan = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), qr_id),
        )
        await db.commit()

    # разрешаем доступ только в нужный раздел
    request.session["allowed_page"] = f"/dashboard/qr/{qr_id}"

    return RedirectResponse(row[0], status_code=302)

# --- Статистика ---
@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats(request: Request):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, title, url, filename, scan_count, last_scan FROM qr_codes"
        )
        stats_list = await cursor.fetchall()

    return templates.TemplateResponse(
        "stats.html",
        {"request": request, "stats_list": stats_list},
    )
