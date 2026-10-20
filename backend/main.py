from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import os, json, httpx, re
from urllib.parse import unquote, quote
from datetime import datetime

from database import engine, Base, Generation, User, get_db, SessionLocal
from tasks import celery_app, generate_design_task

app = FastAPI(title="Black Hole Studio AI", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class RoomRequest(BaseModel):
    user_id: int
    room_type: str = "custom"
    area: float
    height: float
    style: str
    description: str

class UserCreate(BaseModel):
    username: str

ROOM_NAMES = {"living": "Гостиная", "bedroom": "Спальня", "kitchen": "Кухня", "office": "Кабинет", "custom": "Комната"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

def clean(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s or '')).strip()

def decode_ddg(url):
    if 'uddg=' in url:
        m = re.search(r'uddg=([^&]+)', url)
        if m:
            return unquote(m.group(1))
    if url.startswith('//'):
        return 'https:' + url
    return url

def parse_ddg(html):
    out = []
    blocks = re.split(r'<div[^>]+class="result[^"]*"', html)[1:]
    for b in blocks:
        a = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.DOTALL)
        s = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', b, re.DOTALL)
        if a:
            url = decode_ddg(a.group(1))
            title = clean(a.group(2))
            if url and title and 'duckduckgo.com' not in url and len(title) > 3:
                out.append({"title": title[:140], "text": clean(s.group(1))[:200] if s else "", "url": url})
    return out

def parse_mojeek(html):
    out = []
    blocks = re.split(r'<li[^>]*class="result', html)[1:]
    for b in blocks:
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.DOTALL)
        s = re.search(r'<p[^>]+class="s"[^>]*>(.*?)</p>', b, re.DOTALL)
        if a:
            url = a.group(1)
            title = clean(a.group(2))
            if url.startswith('http') and title and len(title) > 3:
                out.append({"title": title[:140], "text": clean(s.group(1))[:200] if s else "", "url": url})
    return out

def parse_bing(html):
    out = []
    blocks = re.split(r'<li[^>]*class="b_algo"', html)[1:]
    for b in blocks:
        a = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', b, re.DOTALL)
        s = re.search(r'<p[^>]*>(.*?)</p>', b, re.DOTALL)
        if a:
            url = a.group(1)
            title = clean(a.group(2))
            if url.startswith('http') and title and len(title) > 3:
                out.append({"title": title[:140], "text": clean(s.group(1))[:200] if s else "", "url": url})
    return out

SOURCES = [
    ("ddg", "https://html.duckduckgo.com/html/?q={q}&kl=ru-ru", parse_ddg),
    ("mojeek", "https://www.mojeek.com/search?q={q}", parse_mojeek),
    ("bing", "https://www.bing.com/search?q={q}&setlang=ru", parse_bing),
]

@app.get("/search")
async def search_furniture(q: str = Query(..., min_length=1, max_length=200)):
    results, seen = [], set()
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=HEADERS) as client:
        for name, tmpl, parser in SOURCES:
            if len(results) >= 8:
                break
            try:
                r = await client.get(tmpl.format(q=quote(q)))
                if r.status_code != 200:
                    print(f"⚠️ {name}: HTTP {r.status_code}")
                    continue
                items = parser(r.text)
                added = 0
                for it in items:
                    if it["url"] in seen:
                        continue
                    seen.add(it["url"])
                    results.append(it)
                    added += 1
                    if len(results) >= 8:
                        break
                print(f"✅ {name}: +{added} (всего {len(results)})")
            except Exception as e:
                print(f"❌ {name}: {e}")
    return {"query": q, "results": results, "duckduckgo_url": f"https://duckduckgo.com/?q={quote(q)}&kl=ru-ru"}

@app.post("/users")
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == user.username).first():
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    new_user = User(username=user.username)
    db.add(new_user); db.commit(); db.refresh(new_user)
    return {"id": new_user.id, "username": new_user.username}

@app.get("/users")
async def get_users(db: Session = Depends(get_db)):
    return [{"id": u.id, "username": u.username} for u in db.query(User).order_by(User.created_at.desc()).all()]

@app.delete("/users/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="Пользователь не найден")
    db.delete(user); db.commit()
    return {"message": "Пользователь удалён"}

@app.post("/generate-design")
async def generate_design(request: RoomRequest, db: Session = Depends(get_db)):
    if not db.query(User).filter(User.id == request.user_id).first():
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    task = generate_design_task.delay(user_id=request.user_id, room_type=request.room_type, area=request.area, height=request.height, style=request.style, description=request.description)
    return {"task_id": task.id, "status": "queued"}

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str):
    task = celery_app.AsyncResult(task_id)
    if task.state == "PENDING": return {"status": "queued", "message": "Задача в очереди"}
    elif task.state == "PROGRESS": return {"status": "processing", "message": task.info.get("status", "Обработка...")}
    elif task.state == "SUCCESS":
        result = task.result
        if result and result.get("status") == "success":
            db = SessionLocal()
            try:
                gen = Generation(
                    user_id=result.get("user_id"),
                    room_type=result.get("room_type"),
                    area=result.get("area"),
                    height=result.get("height"),
                    style=result.get("style"),
                    description=result.get("description"),
                    furniture_json=json.dumps(result.get("furniture", []), ensure_ascii=False),
                    time_sec=result.get("time_sec", "—"),
                    created_at=datetime.utcnow()
                )
                db.add(gen); db.commit(); db.refresh(gen)
                print(f"✅ Сохранено в БД: id={gen.id}")
            except Exception as e:
                print(f"Ошибка БД: {e}"); db.rollback()
            finally:
                db.close()
        return {"status": "completed", "result": result}
    elif task.state == "FAILURE": return {"status": "failed", "error": str(task.result)}
    return {"status": task.state}

@app.get("/history")
async def get_history(user_id: int = Query(None), db: Session = Depends(get_db)):
    if user_id is None: return []
    generations = db.query(Generation).filter(Generation.user_id == user_id).order_by(Generation.created_at.desc()).limit(20).all()
    return [{"id": gen.id, "user_id": gen.user_id, "username": gen.user.username if gen.user else "Unknown", "room_type": gen.room_type, "room_name": ROOM_NAMES.get(gen.room_type, "Комната"), "area": gen.area, "height": gen.height, "style": gen.style, "description": gen.description, "furniture": json.loads(gen.furniture_json) if gen.furniture_json else [], "time_sec": gen.time_sec, "date": f"{gen.created_at.day:02d}.{gen.created_at.month:02d}.{gen.created_at.year} {gen.created_at.hour:02d}:{gen.created_at.minute:02d}"} for gen in generations]

@app.delete("/history/{generation_id}")
async def delete_generation(generation_id: int, db: Session = Depends(get_db)):
    generation = db.query(Generation).filter(Generation.id == generation_id).first()
    if not generation: raise HTTPException(status_code=404, detail="Запись не найдена")
    db.delete(generation); db.commit()
    return {"message": "Запись удалена"}

@app.delete("/history")
async def clear_history(user_id: int = Query(None), db: Session = Depends(get_db)):
    query = db.query(Generation)
    if user_id is not None: query = query.filter(Generation.user_id == user_id)
    query.delete(); db.commit()
    return {"message": "История очищена"}

@app.get("/health")
async def health_check(): return {"status": "ok", "service": "Black Hole Studio AI"}

@app.get("/")
async def serve_index():
    if os.path.isfile("/web2.html"): return FileResponse("/web2.html", media_type="text/html")
    raise HTTPException(status_code=404, detail="Frontend not found")

@app.get("/libs/{filename}")
async def serve_lib(filename: str):
    lib_path = f"/libs/{filename}"
    if os.path.isfile(lib_path):
        if filename.endswith('.js'): return FileResponse(lib_path, media_type="application/javascript")
        return FileResponse(lib_path)
    raise HTTPException(status_code=404, detail="Library not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
