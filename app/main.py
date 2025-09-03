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
from functools import wraps
from PIL import Image, ImageDraw, ImageFont  # опционально; если шрифта нет — будет fallback

app = FastAPI()

# --- Настройки ---
app.add_middleware(
    SessionMiddleware,
    secret_key="f7d9b6a2c3e14f89d5b0a7c6e2f38d9b1c7f0a5d4e8b3c2a9f6d1e0c5b7a3f2d",
    same_site="lax"
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_NAME = "qr_codes.db"
QR_FOLDER = "static/qr"
FONT_PATH = "static/fonts/RobotoSlab-Bold.ttf"  # если файла нет — используем системный шрифт
ADMIN_CODE = "1990"

os.makedirs(QR_FOLDER, exist_ok=True)


# --- Инициализация БД ---
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS qr_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,              -- название QR
            module TEXT,            -- к какому модулю ведёт (/services|/business|/cleaning)
            filename TEXT,          -- файл изображения QR
            created_at TEXT,
            scans INTEGER DEFAULT 0,
            last_scan TEXT
        )
        """)
        await db.commit()


# ============== АВТОРИЗАЦИЯ АДМИНА ==============
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


# ============== ХЕЛПЕРЫ-ДЕКОРАТОРЫ ==============
def admin_only(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not request.session.get("is_admin"):
            return RedirectResponse(url="/", status_code=303)
        return await func(request, *args, **kwargs)
    return wrapper


def user_only(module_name: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            if request.session.get("user_module") != module_name:
                return RedirectResponse(url="/", status_code=303)
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


# ============== АДМИН-ПАНЕЛЬ ==============
@app.get("/dashboard/qr", response_class=HTMLResponse)
@admin_only
async def qr_page(request: Request):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
        qr_list = await cursor.fetchall()
    return templates.TemplateResponse("qr.html", {"request": request, "qr_list": qr_list, "active": "qr"})


@app.post("/dashboard/qr/create")
@admin_only
async def create_qr(request: Request, name: str = Form(...), module: str = Form(...)):
    """
    Ожидает поля формы:
      - name   (Название)
      - module (одно из: services | business | cleaning)
    """
    module = (module or "").strip().strip("/")
    if module not in {"services", "business", "cleaning"}:
        # Подстрахуем: если пришло что-то иное
        module = "services"

    # 1) создаём запись, получаем id
    filename = f"{uuid.uuid4().hex}.png"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "INSERT INTO qr_codes (name, module, filename, created_at) VALUES (?, ?, ?, ?)",
            (name, module, filename, created_at)
        )
        qr_id = cursor.lastrowid
        await db.commit()

    # 2) генерим QR, который ведёт на /scan/{id}
    qr_target = f"/scan/{qr_id}"
    img = qrcode.make(qr_target).convert("RGB")

    # необязательная подпись сверху (если есть шрифт)
    try:
        font = ImageFont.truetype(FONT_PATH, 28)
        draw_tmp = ImageDraw.Draw(img)
        text_w, text_h = draw_tmp.textbbox((0, 0), name, font=font)[2:]
        new_w = max(img.width, text_w + 40)
        new_h = img.height + text_h + 28
        final = Image.new("RGB", (new_w, new_h), "white")
        draw = ImageDraw.Draw(final)
        draw.text(((new_w - text_w) // 2, 8), name, font=font, fill="black")
        final.paste(img, ((new_w - img.width) // 2, text_h + 16))
        final.save(os.path.join(QR_FOLDER, filename))
    except Exception:
        # если шрифтов/ПИЛ нет — просто сохраняем сам QR
        img.save(os.path.join(QR_FOLDER, filename))

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


@app.get("/dashboard/modules", response_class=HTMLResponse)
@admin_only
async def modules_admin(request: Request):
    return templates.TemplateResponse("modules.html", {"request": request, "active": "modules"})


@app.get("/dashboard/users", response_class=HTMLResponse)
@admin_only
async def users_admin(request: Request):
    return templates.TemplateResponse("users.html", {"request": request, "active": "users"})


@app.get("/dashboard/stats", response_class=HTMLResponse)
@admin_only
async def stats_admin(request: Request):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM qr_codes ORDER BY id DESC")
        stats_list = await cursor.fetchall()
    return templates.TemplateResponse("stats.html", {"request": request, "stats_list": stats_list, "active": "stats"})


@app.get("/dashboard/settings", response_class=HTMLResponse)
@admin_only
async def settings_admin(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})


# ============== СКАНИРОВАНИЕ И ДОСТУП ПОЛЬЗОВАТЕЛЕЙ ==============
@app.get("/scan/{qr_id}")
async def scan_qr(request: Request, qr_id: int):
    """
    Пользователь приходит по QR → мы ставим ему в сессию user_module,
    инкрементим счётчик и отправляем в нужный модуль (/services|/business|/cleaning).
    """
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT module FROM qr_codes WHERE id=?", (qr_id,))
        row = await cursor.fetchone()
        if not row:
            return PlainTextResponse("QR-код не найден", status_code=404)

        module = row[0]
        request.session["user_module"] = module  # привязали пользователя к модулю

        await db.execute(
            "UPDATE qr_codes SET scans = scans + 1, last_scan = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), qr_id)
        )
        await db.commit()

    return RedirectResponse(url=f"/{module}", status_code=302)


# --- Пользовательские модули (строго по сессии user_module) ---
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


# --- robots.txt ---
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return "User-agent: *\nDisallow: /"
