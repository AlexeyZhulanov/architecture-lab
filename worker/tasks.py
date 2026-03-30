import os
import requests
from celery import Celery
from ultralytics import YOLO

# Подключаемся к очереди
app = Celery('tasks', broker='redis://redis:6379/0', backend='redis://redis:6379/0')

# Загружаем модель один раз при старте контейнера, чтобы не тратить время на каждую картинку
MODEL_PATH = '/app/data/weights/yolov8s_pipe.pt'
if os.path.exists(MODEL_PATH):
    model = YOLO(MODEL_PATH)
else:
    model = None

BOT_TOKEN = os.getenv("BOT_TOKEN")


@app.task(name='process_pipe_defect')
def process_pipe_defect(file_path, user_id):
    if model is None:
        return "Ошибка: Модель не найдена на сервере."

    # Прогоняем картинку через нейросеть
    results = model(file_path)

    # Сохраняем результат с нарисованными рамками
    result_path = file_path.replace('.jpg', '_result.jpg')
    results[0].save(filename=result_path)

    # Отправляем результат напрямую пользователю в Telegram
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    with open(result_path, 'rb') as photo:
        requests.post(url, data={'chat_id': user_id}, files={'photo': photo})

    # Удаляем временные файлы
    os.remove(file_path)
    os.remove(result_path)

    return "Успешно обработано"