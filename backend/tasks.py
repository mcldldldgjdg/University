from celery import Celery
import httpx
import json
import random
import os
import re
from datetime import datetime

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery("tasks", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
SD_HOST = os.getenv("SD_HOST", "http://stable-diffusion:7860")

ROOM_NAMES = {
    "living": "Гостиная",
    "bedroom": "Спальня",
    "kitchen": "Кухня",
    "office": "Кабинет",
    "custom": "Комната"
}

ROOM_DIMENSIONS = {
    "living": {"width": 5, "depth": 4, "height": 2.7},
    "bedroom": {"width": 4, "depth": 3.75, "height": 2.7},
    "kitchen": {"width": 4, "depth": 3, "height": 2.7},
    "office": {"width": 3.5, "depth": 2.85, "height": 2.7},
    "custom": {"width": 4.5, "depth": 4, "height": 2.7}
}


def parse_furniture_json(text):
    if not text:
        return []
    strategies = [
        lambda t: re.search(r'```json\s*([\s\S]*?)```', t),
        lambda t: re.search(r'```\s*([\s\S]*?)```', t),
        lambda t: re.search(r'\{[\s\S]*\}', t),
        lambda t: re.search(r'\[[\s\S]*\]', t),
    ]
    for strategy in strategies:
        match = strategy(text)
        if match:
            try:
                data = json.loads(match.group(1) if match.lastindex else match.group(0))
                if isinstance(data, dict) and 'furniture' in data:
                    return data['furniture']
                elif isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                continue
    return []


@celery_app.task(bind=True, max_retries=2)
def generate_design_task(self, user_id, room_type, area, height, style, description):
    room_name = ROOM_NAMES.get(room_type, "Комната")
    room_dims = ROOM_DIMENSIONS.get(room_type, ROOM_DIMENSIONS["custom"])

    # ===== ПАСХАЛКА «ПСИХОДЕЛ»: белый диван + гипсовый конь =====
    # Шанс 1 к 10 000 000 при генерации гостиной; детерминированный
    # триггер для демо — слово "психодел"/"psycho" в пожеланиях.
    _desc = (description or "").lower()
    _trigger = ("психодел" in _desc) or ("psycho" in _desc)
    _is_easter = (room_type == "living") and (_trigger or (random.random() < 1e-7))
    if _is_easter:
        print("🌀 Выпала пасхалка Психодел! trigger=%s" % _trigger)
        _now = datetime.utcnow()
        return {
            "status": "success",
            "model": "🌀 психодел-easter",
            "time": 0,
            "time_sec": "0.0",
            "date": f"{_now.day:02d}.{_now.month:02d}.{_now.year} {_now.hour:02d}:{_now.minute:02d}",
            "furniture": [
                {"item": "Белый диван", "type": "диван",
                 "description": "Монолитный белый диван из микрофибры в абсолютной белизне",
                 "materials": "микрофибра, белый каркас", "dimensions": "220x85x95",
                 "price": 0, "icon": "🛋️"},
                {"item": "Статуя коня", "type": "статуя",
                 "description": "Гипсовая статуя скачущего коня, обращённая к дивану",
                 "materials": "гипс, белый пластик", "dimensions": "60x120x40",
                 "price": 0, "icon": "🐎"},
            ],
            "room_type": room_type, "room_name": room_name,
            "room_dimensions": room_dims, "image_url": None,
            "easter_egg": True, "easter_name": "Психодел",
            "user_id": user_id, "area": area, "height": height,
            "style": style, "description": description,
        }

    self.update_state(state="PROGRESS", meta={"status": "Генерация текста через Qwen..."})

    prompt = (
        f"Ты эксперт по дизайну интерьера. Создай план расстановки мебели для {room_name.lower()}.\n\n"
        f"Параметры комнаты:\n"
        f"- Площадь: {area} м²\n"
        f"- Высота потолков: {height} м\n"
        f"- Стиль: {style}\n"
        f"- Пожелания: {description}\n"
        f"- Размеры комнаты: {room_dims['width']}м x {room_dims['depth']}м\n\n"
        "Выбери 3-4 ключевых предмета мебели.\n"
        "Для каждого предмета укажи:\n"
        "- item: название (на русском)\n"
        "- type: тип мебели (диван, кровать, стол, стул, шкаф, тумба, телевизор, стеллаж, лампа)\n"
        "- description: краткое описание (1 предложение)\n"
        "- materials: основные материалы\n"
        "- dimensions: размеры в формате ШxВxГ см (например 200x90x80)\n"
        "- price: примерная цена в рублях (число)\n"
        "- x: позиция по горизонтали в метрах\n"
        "- y: позиция по вертикали в метрах\n"
        "- icon: эмодзи\n\n"
        'Верни СТРОГО валидный JSON: {"furniture": [{"item": "...", "type": "...", "description": "...", "materials": "...", "dimensions": "100x80x60", "price": 15000, "x": 1.5, "y": 2.0, "icon": "🛋️"}]}'
    )

    try:
        with httpx.Client() as client:
            response = client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": "qwen2.5:3b", "prompt": prompt, "stream": False},
                timeout=180.0,
            )

        if response.status_code != 200:
            raise Exception(f"Ollama статус {response.status_code}")

        result = response.json()
        raw_text = result.get("response", "")
        print(f"Raw response length: {len(raw_text)}")

        furniture = parse_furniture_json(raw_text)
        print(f"Parsed furniture count: {len(furniture)}")

        if not furniture:
            print("⚠️ Не удалось распарсить JSON, создаём заглушку")
            furniture = [
                {"item": "Диван", "type": "диван", "description": "Уютный диван для отдыха", "materials": "ткань, дерево", "dimensions": "200x90x80", "price": 35000, "x": 2.0, "y": 1.5, "icon": "🛋️"},
                {"item": "Журнальный стол", "type": "стол", "description": "Стильный журнальный стол", "materials": "дерево, стекло", "dimensions": "120x60x45", "price": 15000, "x": 2.0, "y": 2.5, "icon": "🍽️"},
                {"item": "Тумба под ТВ", "type": "тумба", "description": "Современная тумба под телевизор", "materials": "МДФ, металл", "dimensions": "150x50x40", "price": 12000, "x": 2.0, "y": 3.2, "icon": "📺"}
            ]

        text_duration = result.get("total_duration", 0)
        self.update_state(state="PROGRESS", meta={"status": "Генерация картинки через SD..."})

        image_url = None
        try:
            sd_prompt = f"{style} interior design, {room_name.lower()}, {description}, professional photography, high quality"
            with httpx.Client() as client:
                sd_response = client.post(
                    f"{SD_HOST}/sdapi/v1/txt2img",
                    json={"prompt": sd_prompt, "negative_prompt": "ugly, blurry, low quality", "steps": 20, "width": 512, "height": 512},
                    timeout=180.0,
                )
            if sd_response.status_code == 200:
                sd_result = sd_response.json()
                if sd_result.get("images"):
                    image_url = f"data:image/png;base64,{sd_result['images'][0]}"
        except Exception as e:
            print(f"SD недоступен: {e}")

        time_sec = str(round(text_duration / 1e9, 1)) if text_duration else "—"
        now = datetime.utcnow()
        date_str = f"{now.day:02d}.{now.month:02d}.{now.year} {now.hour:02d}:{now.minute:02d}"

        return {
            "status": "success",
            "model": "qwen2.5:3b + SD",
            "time": text_duration,
            "furniture": furniture,
            "time_sec": time_sec,
            "date": date_str,
            "room_type": room_type,
            "room_name": room_name,
            "room_dimensions": room_dims,
            "image_url": image_url,
            "user_id": user_id,
            "area": area,
            "height": height,
            "style": style,
            "description": description
        }

    except Exception as exc:
        print(f"Task error: {exc}")
        raise self.retry(exc=exc, countdown=5)
