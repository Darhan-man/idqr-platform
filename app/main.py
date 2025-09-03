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

# Секретный ключ для сессий
app.add_middleware(
    SessionMiddleware,
    secret_key="f7d9b6a2c3e14f89d5b0a7c6e2f38d9b1c7f0a5d4e8b3c2a9f6d1e0c5b7a3f2d"
)

# Статика и шаблоны
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_NAME = "qr_codes.db"

# --- База ---
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS qr_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            link TEXT,
            created_at TEXT,
            scans INTEGER DEFAULT 0,
            last_scan TEXT
        )
        """)
        await db.commit()


# --- Главная страница (вход) ---
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    if code == "1990":  # 🔑 тут можно заменить на свой пароль 
        request.session["is_admin"] = True
        return RedirectResponse(url="/dashboard/stats", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный код"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")


# --- Панель статистики ---
@app.get("/dashboard/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM qr_codes")
        stats_list = await cursor.fetchall()

    return templates.TemplateResponse("stats.html", {"request": request, "stats_list": stats_list})


# --- Генерация QR ---
@app.post("/dashboard/qr/create")
async def create_qr(request: Request, name: str = Form(...), link: str = Form(...)):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/")

    filename = f"{uuid.uuid4().hex}.png"
    filepath = os.path.join("static/qr", filename)

    img = qrcode.make(link)
    img.save(filepath)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO qr_codes (name, link, created_at) VALUES (?, ?, ?)",
            (name, f"/static/qr/{filename}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.commit()

    return RedirectResponse(url="/dashboard/stats", status_code=303)


# --- Сканирование QR ---
@app.get("/scan/{qr_id}")
async def scan_qr(qr_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT link FROM qr_codes WHERE id=?", (qr_id,))
        qr = await cursor.fetchone()
        if not qr:
            return PlainTextResponse("QR-код не найден", status_code=404)

        await db.execute(
            "UPDATE qr_codes SET scans = scans + 1, last_scan = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), qr_id)
        )
        await db.commit()

    return RedirectResponse(url=qr[0])


# --- robots.txt ---
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return "User-agent: *\nDisallow: /"
