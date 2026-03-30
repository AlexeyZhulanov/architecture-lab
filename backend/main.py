from fastapi import FastAPI, UploadFile, File
from celery import Celery
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