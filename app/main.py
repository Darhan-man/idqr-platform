from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import aiosqlite
import qrcode
import os
import uuid
from datetime import datetime

app = FastAPI()

# --- Настройки ---
app.add_middleware(
    SessionMiddleware,
    secret_key="f7d9b6a2c3e14f89d5b0a7c6e2f38d9b1c7f0a5d4e8b3c2a9f6d1e0c5b7a3f2d"
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_NAME = "qr_codes.db"
QR_FOLDER = "static/qr"
ADMIN_CODE = "1990"

os.makedirs(QR_FOLDER, exist_ok=True)

# --- Инициализация БД ---
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS qr_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            module TEXT,
            filename TEXT,
            created_at TEXT,
            scans INTEGER DEFAULT 0,
            last_scan TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS qr_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qr_id INTEGER,
            name TEXT,
            module TEXT,
            scanned_at TEXT
        )
        """)
        await db.commit()

# --- Главная страница (логин админа) ---
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == ADMIN_CODE:
        request.session["is_admin"] = True
        return RedirectResponse(url="/dashboard/qr", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный код"})

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

# --- Декораторы ---
def admin_only(func):
    async def wrapper(request: Request, *args, **kwargs):
        if not request.session.get("is_admin"):
            return RedirectResponse(url="/")
        return await func(request, *args, **kwargs)
    return wrapper

def user_only(module_name: str):
    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            if request.session.get("user_module") != module_name:
                return RedirectResponse(url="/")
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator

# --- Панель QR-кодов ---
@app.get("/dashboard/qr", response_class=HTMLResponse)
@admin_only
async def qr_page(request: Request):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
        qr_list = await cursor.fetchall()
    return templates.TemplateResponse("qr.html", {"request": request, "qr_list": qr_list})

@app.post("/dashboard/qr/create")
@admin_only
async def create_qr(request: Request, name: str = Form(...), module: str = Form(...)):
    filename = f"{uuid.uuid4().hex}.png"
    filepath = os.path.join(QR_FOLDER, filename)

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "INSERT INTO qr_codes (name, module, filename, created_at) VALUES (?, ?, ?, ?)",
            (name, module, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        qr_id = cursor.lastrowid
        await db.commit()

    # Генерация QR с ссылкой на сканирование
    qr_url = f"/scan/{qr_id}"
    img = qrcode.make(qr_url).convert("RGB")
    img.save(filepath)

    return RedirectResponse(url="/dashboard/qr", status_code=303)

@app.get("/delete_qr/{qr_id}")
@admin_only
async def delete_qr(request: Request, qr_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT filename FROM qr_codes WHERE id=?", (qr_id,))
        row = await cursor.fetchone()
        if row:
            filepath = os.path.join(QR_FOLDER, row[0])
            if os.path.exists(filepath):
                os.remove(filepath)
            await db.execute("DELETE FROM qr_codes WHERE id=?", (qr_id,))
            await db.commit()
    return RedirectResponse(url="/dashboard/qr", status_code=303)

# --- Сканирование QR-кода ---
@app.get("/scan/{qr_id}")
async def scan_qr(request: Request, qr_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT name, module FROM qr_codes WHERE id=?", (qr_id,))
        qr = await cursor.fetchone()
        if not qr:
            return PlainTextResponse("QR-код не найден", status_code=404)
        name, module = qr

        # Сохраняем сессию пользователя
        request.session["user_module"] = module

        # Обновляем статистику
        await db.execute(
            "UPDATE qr_codes SET scans = scans + 1, last_scan = ? WHERE id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), qr_id)
        )
        await db.execute(
            "INSERT INTO qr_scans (qr_id, name, module, scanned_at) VALUES (?, ?, ?, ?)",
            (qr_id, name, module, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()

    return RedirectResponse(url=f"/{module}")

# --- Пользовательские модули ---
@app.get("/services", response_class=HTMLResponse)
@user_only("services")
async def services_page(request: Request):
    return templates.TemplateResponse("services.html", {"request": request})

@app.get("/business", response_class=HTMLResponse)
@user_only("business")
async def business_page(request: Request):
    return templates.TemplateResponse("business.html", {"request": request})

@app.get("/cleaning", response_class=HTMLResponse)
@user_only("cleaning")
async def cleaning_page(request: Request):
    return templates.TemplateResponse("cleaning.html", {"request": request})

# --- Статистика ---
@app.get("/dashboard/stats", response_class=HTMLResponse)
@admin_only
async def stats_page(request: Request):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
        qr_list = await cursor.fetchall()
        cursor = await db.execute("SELECT * FROM qr_scans ORDER BY id DESC")
        scan_list = await cursor.fetchall()
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "qr_list": qr_list,
        "scan_list": scan_list
    })

# --- robots.txt ---
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return "User-agent: *\nDisallow: /"
