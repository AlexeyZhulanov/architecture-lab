from fastapi import FastAPI, UploadFile, File, Body
from celery import Celery
from typing import List
import os
import shutil
import redis
import logging

# Настройка логирования
logging.basicConfig(
    filename='/app/logs/system_load.log',
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)

app = FastAPI(title="Pipe Defect API")

redis_client = redis.Redis(host='redis', port=6379, db=0)

# Подключаемся к очереди Redis
celery_app = Celery('tasks', broker='redis://redis:6379/0', backend='redis://redis:6379/0')

# Лимиты нагрузки
CONCURRENCY = 5
PHOTO_HARD_LIMIT = 100
MAX_VIDEO_SIZE = 50 * 1024 * 1024  # 50 МБ
VIDEO_HARD_LIMIT = 10

TEMP_DIR = "/app/data/temp"
NEW_DATA_IMG = "/app/data/new_data/images"
NEW_DATA_LBL = "/app/data/new_data/labels"
QUARANTINE_DIR = "/app/data/quarantine"


@app.post("/process-batch/")
async def process_batch(user_id: str, files: List[UploadFile] = File(...)):
    logging.info(f"BATCH_REQUEST - User {user_id} sent {len(files)} images")

    # Проверяем текущую нагрузку
    i = celery_app.control.inspect()
    active_tasks = i.active()
    current_active = sum(len(tasks) for worker, tasks in active_tasks.items()) if active_tasks else 0

    # Сколько задач ждет в очереди Redis
    queue_length = redis_client.llen('celery')

    # ANTI-DDOS / Защита от исчерпания ресурсов (API Abuse)
    # Нормальный трафик из Telegram (пачки по 10) никогда не пробьет этот лимит
    if queue_length + len(files) > PHOTO_HARD_LIMIT:
        logging.critical(f"SECURITY - Anti-DDoS triggered. Queue full ({queue_length} + {len(files)} > {PHOTO_HARD_LIMIT}). DROP Malicious Batch from User {user_id}.")
        return {"status": "error", "message": "Сервер отклонил подозрительно большой пакет данных (Anti-DDoS)."}

    available_slots = max(0, CONCURRENCY - current_active)
    to_processing = min(available_slots, len(files))
    to_queue = len(files) - to_processing
    logging.info(f"LOAD_BALANCER - Batch accepted. {to_processing} to GPU immediately, {to_queue} queued (positions {queue_length + 1} to {queue_length + to_queue}).")

    # Обработка файлов и распределение
    for file in files:
        file_path = f"/app/data/{file.filename}"
        with open(file_path, "wb") as buffer:
            buffer.write(await file.read())

        celery_app.send_task('process_pipe_defect', args=[file_path, user_id])

    return {"status": "ok"}


@app.post("/process-video/")
async def process_video(user_id: str, file: UploadFile = File(...)):
    logging.info(f"VIDEO_REQUEST - User {user_id} sent video {file.filename}")

    # В FastAPI file.size возвращает размер в байтах
    if file.size and file.size > MAX_VIDEO_SIZE:
        logging.warning(f"SECURITY - Video {file.filename} from User {user_id} rejected. Size > 50MB.")
        return {"status": "error", "message": "❌ Файл слишком большой. Максимальный размер видео: 50 МБ."}

    # Отдельная очередь только для видео
    video_queue_len = redis_client.llen('video_queue')

    if video_queue_len >= VIDEO_HARD_LIMIT:
        logging.critical(f"LOAD_BALANCER - Video queue full ({video_queue_len}/{VIDEO_HARD_LIMIT}).")
        return {"status": "error", "message": "❌ Очередь видео переполнена. Попробуйте позже."}

    # Сохраняем видео на диск
    file_path = f"/app/data/{file.filename}"
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    logging.info(f"LOAD_BALANCER - Video {file.filename} accepted. Queue pos: {video_queue_len + 1}")

    # Отправляем в выделенную очередь
    celery_app.send_task('process_pipe_video', args=[file_path, user_id], queue='video_queue')

    return {"status": "ok"}


@app.post("/feedback")
async def handle_feedback(data: dict = Body(...)):
    file_id = data.get("file_id")
    status = data.get("status")

    # Полные пути ко всем связанным файлам
    src_img_video = f"/app/data/temp/{file_id}.jpg"
    src_img_photo = f"/app/data/{file_id}.jpg"

    if os.path.exists(src_img_video):
        src_img = src_img_video
    elif os.path.exists(src_img_photo):
        src_img = src_img_photo
    else:
        return {"status": "Файл уже перенесен или удален."}

    src_lbl = f"{TEMP_DIR}/{file_id}.txt"  # Разметка
    res_img = f"{TEMP_DIR}/{file_id}_res.jpg"  # Картинка с рамками для Телеграма

    os.makedirs(NEW_DATA_IMG, exist_ok=True)
    os.makedirs(NEW_DATA_LBL, exist_ok=True)
    os.makedirs(QUARANTINE_DIR, exist_ok=True)

    if status == "confirm":
        if os.path.exists(src_img):
            shutil.move(src_img, f"{NEW_DATA_IMG}/{file_id}.jpg")
        if os.path.exists(src_lbl):
            shutil.move(src_lbl, f"{NEW_DATA_LBL}/{file_id}.txt")
        if os.path.exists(res_img):
            os.remove(res_img)
        logging.info(f"FEEDBACK - User confirmed {file_id}. Moved to training set.")
        return {"status": "added_to_training_set"}

    elif status == "reject":
        if os.path.exists(src_img):
            open(src_lbl, 'w').close()  # Очищаем файл для Background Image
            shutil.move(src_img, f"{NEW_DATA_IMG}/{file_id}.jpg")
            shutil.move(src_lbl, f"{NEW_DATA_LBL}/{file_id}.txt")
        if os.path.exists(res_img):
            os.remove(res_img)
        logging.info(f"FEEDBACK - False Positive {file_id}. Added as Background Image.")
        return {"status": "added_as_negative_sample"}

    else: # inaccurate
        if os.path.exists(src_img):
            shutil.move(src_img, f"{QUARANTINE_DIR}/{file_id}.jpg")
        if os.path.exists(src_lbl):
            os.remove(src_lbl)
        if os.path.exists(res_img):
            os.remove(res_img)
        logging.warning(f"FEEDBACK - Quarantine: {file_id} has inaccurate bounding box.")
        return {"status": "moved_to_quarantine"}


@app.get("/stats")
async def get_stats():
    # Считаем общее количество запросов по логам
    total_requests = 0
    log_path = '/app/logs/system_load.log'
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            total_requests = sum(1 for line in f if "NEW_REQUEST" in line)

    # Считаем подтвержденные дефекты (которые ждут дообучения)
    pending_images = len(os.listdir(NEW_DATA_IMG)) if os.path.exists(NEW_DATA_IMG) else 0

    # Считаем количество завершенных циклов дообучения (папки в архиве)
    retrain_cycles = 0
    archive_dir = "/app/data/archive"
    if os.path.exists(archive_dir):
        retrain_cycles = len([name for name in os.listdir(archive_dir) if os.path.isdir(os.path.join(archive_dir, name))])

    # Считаем количество дефектов в карантине
    quarantine_items = len(os.listdir(QUARANTINE_DIR)) if os.path.exists(QUARANTINE_DIR) else 0

    return {
        "total_requests": total_requests,
        "pending_images": pending_images,
        "retrain_cycles": retrain_cycles,
        "quarantine_items": quarantine_items
    }