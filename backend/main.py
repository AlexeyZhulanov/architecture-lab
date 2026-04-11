from fastapi import FastAPI, UploadFile, File, Body
from celery import Celery
import os
import shutil
import logging

# Настройка логирования
logging.basicConfig(
    filename='/app/logs/system_load.log',
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)

app = FastAPI(title="Pipe Defect API")

# Подключаемся к очереди Redis
celery_app = Celery('tasks', broker='redis://redis:6379/0', backend='redis://redis:6379/0')

# Лимит нагрузки: видеокарта тянет максимум 5 задач одновременно (видеопамять)
MAX_ACTIVE_TASKS = 5

TEMP_DIR = "/app/data/temp"
NEW_DATA_IMG = "/app/data/new_data/images"
NEW_DATA_LBL = "/app/data/new_data/labels"


@app.post("/process-image/")
async def process_image(user_id: str, file: UploadFile = File(...)):
    logging.info(f"NEW_REQUEST - User {user_id} sent {file.filename}")

    file_path = f"/app/data/{file.filename}"
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    # Проверяем текущую нагрузку
    i = celery_app.control.inspect()
    active_tasks = i.active()

    current_load = 0
    if active_tasks:
        for worker, tasks in active_tasks.items():
            current_load += len(tasks)

    # Логика распределения нагрузки
    if current_load >= MAX_ACTIVE_TASKS:
        logging.warning(f"LOAD_BALANCER - Active tasks: {current_load}/{MAX_ACTIVE_TASKS}. Peak load reached!")
        logging.info(f"QUEUE - Request from User {user_id} placed in queue.")
        status_message = "Сервер перегружен. Ваша задача поставлена в очередь."
    else:
        logging.info(f"LOAD_BALANCER - Active tasks: {current_load}/{MAX_ACTIVE_TASKS}. Processing...")
        status_message = "Задача принята в обработку."

    # Отправляем задачу в очередь (Celery)
    task = celery_app.send_task('process_pipe_defect', args=[file_path, user_id])

    return {"status": status_message, "task_id": task.id}


@app.post("/feedback")
async def handle_feedback(data: dict = Body(...)):
    file_id = data.get("file_id")
    confirmed = data.get("confirmed")

    # Полные пути ко всем связанным файлам
    src_img = f"/app/data/{file_id}.jpg"  # Оригинал
    src_lbl = f"{TEMP_DIR}/{file_id}.txt"  # Разметка
    res_img = f"{TEMP_DIR}/{file_id}_res.jpg"  # Картинка с рамками для Телеграма

    if confirmed:
        # Переносим полезные данные для дообучения
        os.makedirs(NEW_DATA_IMG, exist_ok=True)
        os.makedirs(NEW_DATA_LBL, exist_ok=True)

        if os.path.exists(src_img):
            shutil.move(src_img, f"{NEW_DATA_IMG}/{file_id}.jpg")
        if os.path.exists(src_lbl):
            shutil.move(src_lbl, f"{NEW_DATA_LBL}/{file_id}.txt")

        # Картинка с рамками для обучения не нужна
        if os.path.exists(res_img):
            os.remove(res_img)

        logging.info(f"FEEDBACK - User confirmed {file_id}. Moved to training set.")
        return {"status": "added_to_training_set"}

    else:
        # Полная очистка всех следов ложного срабатывания
        if os.path.exists(src_img):
            os.remove(src_img)
        if os.path.exists(src_lbl):
            os.remove(src_lbl)
        if os.path.exists(res_img):
            os.remove(res_img)

        logging.info(f"FEEDBACK - User rejected {file_id}. All temp files cleaned.")
        return {"status": "ignored_and_cleaned"}


@app.get("/stats")
async def get_stats():
    # Считаем общее количество запросов по логам
    total_requests = 0
    log_path = '/app/logs/system_load.log'
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            total_requests = sum(1 for line in f if "NEW_REQUEST" in line)

    # Считаем подтвержденные дефекты (которые ждут дообучения)
    pending_images = 0
    if os.path.exists(NEW_DATA_IMG):
        pending_images = len(
            [name for name in os.listdir(NEW_DATA_IMG) if os.path.isfile(os.path.join(NEW_DATA_IMG, name))])

    # Считаем количество завершенных циклов дообучения (папки в архиве)
    retrain_cycles = 0
    archive_dir = "/app/data/archive"
    if os.path.exists(archive_dir):
        retrain_cycles = len([name for name in os.listdir(archive_dir) if os.path.isdir(os.path.join(archive_dir, name))])

    return {
        "total_requests": total_requests,
        "pending_images": pending_images,
        "retrain_cycles": retrain_cycles
    }