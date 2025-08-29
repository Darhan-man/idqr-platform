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


# --- ИНИЦИАЛИЗАЦИЯ БД ---
async def init_db():
    async with aiosqlite.connect("qr_codes.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS qrcodes (
                id TEXT PRIMARY KEY,
                title TEXT,
                data TEXT,
                filename TEXT,
                created_at TEXT,
                scan_count INTEGER DEFAULT 0,
                last_scan TEXT
            )
        """)
        await db.commit()

@app.on_event("startup")
async def on_startup():
    await init_db()


# --- ГЛАВНАЯ СТРАНИЦА ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    async with aiosqlite.connect("qr_codes.db") as db:
        cursor = await db.execute("SELECT id, title, filename, scan_count FROM qrcodes ORDER BY created_at DESC")
        rows = await cursor.fetchall()
    return templates.TemplateResponse("qr.html", {"request": request, "qrcodes": rows})


# --- СОЗДАНИЕ НОВОГО QR ---
@app.post("/create_qr")
async def create_qr(title: str = Form(...), qrdata: str = Form(...)):
    qr_id = str(uuid.uuid4())
    filename = f"{qr_id}.png"
    filepath = os.path.join("static", filename)

    # Генерация QR (прямая ссылка, без внутреннего редиректа)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(qrdata)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # Добавляем текст под QR
    draw = ImageDraw.Draw(qr_img)
    font = ImageFont.load_default()
    text_w, text_h = draw.textsize(title, font=font)
    img_w, img_h = qr_img.size
    new_img = Image.new("RGB", (img_w, img_h + text_h + 10), "white")
    new_img.paste(qr_img, (0, 0))
    draw = ImageDraw.Draw(new_img)
    draw.text(((img_w - text_w) / 2, img_h + 5), title, font=font, fill="black")

    new_img.save(filepath)

    # Сохраняем в БД
    async with aiosqlite.connect("qr_codes.db") as db:
        await db.execute(
            "INSERT INTO qrcodes (id, title, data, filename, created_at) VALUES (?, ?, ?, ?, ?)",
            (qr_id, title, qrdata, filename, datetime.now().isoformat())
        )
        await db.commit()

    return RedirectResponse("/", status_code=303)


# --- СКАНИРОВАНИЕ QR ---
@app.get("/scan/{qr_id}")
async def scan_qr(qr_id: str):
    async with aiosqlite.connect("qr_codes.db") as db:
        cursor = await db.execute("SELECT data, scan_count FROM qrcodes WHERE id = ?", (qr_id,))
        row = await cursor.fetchone()
        if row:
            data, scan_count = row
            await db.execute(
                "UPDATE qrcodes SET scan_count = ?, last_scan = ? WHERE id = ?",
                (scan_count + 1, datetime.now().isoformat(), qr_id)
            )
            await db.commit()
            return RedirectResponse(data)  # редирект на оригинальную ссылку
    return RedirectResponse("/", status_code=303)


# --- СТАТИСТИКА ---
@app.get("/stats", response_class=HTMLResponse)
async def stats(request: Request):
    async with aiosqlite.connect("qr_codes.db") as db:
        cursor = await db.execute(
            "SELECT id, title, scan_count, last_scan FROM qrcodes ORDER BY scan_count DESC"
        )
        rows = await cursor.fetchall()
    return templates.TemplateResponse("stats.html", {"request": request, "stats": rows})
