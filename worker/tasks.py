import os
import requests
import json
from celery import Celery
from celery.schedules import crontab
from ultralytics import YOLO
from continual_learning import run_continual_learning

# Подключаемся к очереди
app = Celery('tasks', broker='redis://redis:6379/0', backend='redis://redis:6379/0')

# Настройка планировщика
app.conf.beat_schedule = {
    'check-for-retraining-every-10-minutes': {
        'task': 'check_and_retrain',
        'schedule': crontab(minute='*/10'), # Запуск каждые 10 минут
    },
}

@app.task(name='check_and_retrain')
def check_and_retrain():
    """Эта задача вызывается по расписанию и запускает процесс проверки новых данных"""
    run_continual_learning()

# Загружаем модель один раз при старте контейнера, чтобы не тратить время на каждую картинку
MODEL_PATH = '/app/data/weights/yolov8s_pipe.pt'
model = YOLO(MODEL_PATH) if os.path.exists(MODEL_PATH) else None
BOT_TOKEN = os.getenv("BOT_TOKEN")


@app.task(name='process_pipe_defect')
def process_pipe_defect(file_path, user_id):
    if model is None:
        return "Ошибка: Модель не найдена на сервере."

    # Прогоняем картинку через нейросеть
    results = model(file_path)
    result = results[0]

    # Генерируем уникальный ID для этой пары фото+разметка
    file_id = os.path.basename(file_path).split('.')[0]

    # Если объекты найдены, сохраняем их координаты
    label_path = f"/app/data/temp/{file_id}.txt"
    os.makedirs("/app/data/temp", exist_ok=True)

    with open(label_path, 'w') as f:
        for box in result.boxes:
            # Координаты в формате YOLO: class x_center y_center width height (normalized)
            coords = box.xywhn[0].tolist()
            class_id = int(box.cls[0])
            f.write(f"{class_id} {' '.join(map(str, coords))}\n")

    # Сохраняем результат с нарисованными рамками
    res_img_path = f"/app/data/temp/{file_id}_res.jpg"
    result.save(filename=res_img_path)

    # Отправляем фото с Inline кнопками пользователю в Telegram
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    # Кодируем callback_data: действие|ID_файла
    keyboard = {
        "inline_keyboard": [
            [{"text": "✅ Верно", "callback_data": f"confirm|{file_id}"}],
            [{"text": "⚠️ Неточная рамка", "callback_data": f"inaccurate|{file_id}"}],
            [{"text": "❌ Ложное срабатывание", "callback_data": f"reject|{file_id}"}]
        ]
    }

    with open(res_img_path, 'rb') as photo:
        response = requests.post(url, data={
            'chat_id': user_id,
            'caption': "Результат детекции. Пожалуйста, подтвердите качество разметки:",
            'reply_markup': json.dumps(keyboard)
        }, files={'photo': photo})

        # Если Telegram вернет ошибку, это будет видно в логах
        response.raise_for_status()

    return f"Успешно обработано {file_id}"