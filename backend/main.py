from fastapi import FastAPI, UploadFile, File, Body
from celery import Celery
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
HARD_LIMIT = 20

TEMP_DIR = "/app/data/temp"
NEW_DATA_IMG = "/app/data/new_data/images"
NEW_DATA_LBL = "/app/data/new_data/labels"
QUARANTINE_DIR = "/app/data/quarantine"


@app.post("/process-image/")
async def process_image(user_id: str, file: UploadFile = File(...)):
    logging.info(f"NEW_REQUEST - User {user_id} sent {file.filename}")

    file_path = f"/app/data/{file.filename}"
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    # Проверяем текущую нагрузку
    i = celery_app.control.inspect()
    active_tasks = i.active()
    current_active = sum(len(tasks) for worker, tasks in active_tasks.items()) if active_tasks else 0

    # Сколько задач ждет в очереди Redis
    queue_length = redis_client.llen('celery')

    # Логика распределения нагрузки
    if queue_length >= HARD_LIMIT:
        # Сценарий 1: Hard Limit (Load Shedding) - Полный сброс нагрузки
        logging.critical(f"LOAD_BALANCER - Queue full ({queue_length}/{HARD_LIMIT}). DROP Request from User {user_id}.")
        os.remove(file_path)  # Удаляем файл, сервер отказывается его обрабатывать
        return {"status": "❌ Сервер перегружен (превышен жесткий лимит). Отказ в обслуживании."}

    elif current_active >= CONCURRENCY:
        # Сценарий 2: Soft Limit - Видеокарта занята, ставим в очередь
        position = queue_length + 1
        logging.info(
            f"QUEUE - Active: {current_active}/{CONCURRENCY}. Task from User {user_id} queued at pos {position}.")
        status_message = f"⏳ Сервер сейчас занят. Вы поставлены в очередь (ваше место: {position})."

        # Отправляем задачу в очередь
        task = celery_app.send_task('process_pipe_defect', args=[file_path, user_id])
        return {"status": status_message, "task_id": task.id}

    else:
        # Сценарий 3: Штатная работа - Есть свободный слот в GPU
        logging.info(
            f"PROCESSING - Active: {current_active}/{CONCURRENCY}. Task from User {user_id} processing immediately.")
        status_message = "✅ Свободный слот найден. Задача мгновенно принята в обработку."

        # Отправляем задачу
        task = celery_app.send_task('process_pipe_defect', args=[file_path, user_id])
        return {"status": status_message, "task_id": task.id}


@app.post("/feedback")
async def handle_feedback(data: dict = Body(...)):
    file_id = data.get("file_id")
    status = data.get("status")

    # Полные пути ко всем связанным файлам
    src_img = f"/app/data/{file_id}.jpg"  # Оригинал
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