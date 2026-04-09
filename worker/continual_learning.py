import os
import shutil
import yaml
from ultralytics import YOLO
import logging
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    filename='/app/logs/system_load.log',
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)

# Пути внутри Docker-контейнера
NEW_DATA_DIR = '/app/data/new_data'
IMAGES_DIR = os.path.join(NEW_DATA_DIR, 'images')
LABELS_DIR = os.path.join(NEW_DATA_DIR, 'labels')
WEIGHTS_PATH = '/app/data/weights/yolov8s_pipe.pt'
ARCHIVE_DIR = '/app/data/archive'

# Порог для старта дообучения
THRESHOLD = 5


def create_yaml_for_training():
    """Создает временный data.yaml для новых данных"""
    yaml_path = os.path.join(NEW_DATA_DIR, 'retrain_data.yaml')
    # todo Сделать правильную структуру, пока что это заглушка
    data = {
        'path': NEW_DATA_DIR,
        'train': 'images',
        'val': 'images',
        'names': {0: 'defect'}
    }
    with open(yaml_path, 'w') as f:
        yaml.dump(data, f)
    return yaml_path


def archive_used_data():
    """Переносит отработанные данные в архив, чтобы не обучать на них повторно"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_folder = os.path.join(ARCHIVE_DIR, f"batch_{timestamp}")
    os.makedirs(archive_folder, exist_ok=True)

    for folder in ['images', 'labels']:
        src = os.path.join(NEW_DATA_DIR, folder)
        dst = os.path.join(archive_folder, folder)
        shutil.copytree(src, dst)

        # Очищаем оригинальную папку
        for file in os.listdir(src):
            os.remove(os.path.join(src, file))


def run_continual_learning():
    # Проверяем, есть ли папки
    if not os.path.exists(IMAGES_DIR) or not os.path.exists(LABELS_DIR):
        os.makedirs(IMAGES_DIR, exist_ok=True)
        os.makedirs(LABELS_DIR, exist_ok=True)
        return

    # Считаем количество новых изображений
    images = [f for f in os.listdir(IMAGES_DIR) if f.endswith(('.jpg', '.png'))]

    if len(images) < THRESHOLD:
        print(f"Недостаточно данных для дообучения. Текущее количество: {len(images)}/{THRESHOLD}")
        return

    logging.info(f"RETRAINING - Started auto-retraining on {len(images)} new images.")
    print(f"Начинаем дообучение на {len(images)} новых файлах...")

    # Создаем конфиг
    yaml_path = create_yaml_for_training()

    # Загружаем текущую модель
    if not os.path.exists(WEIGHTS_PATH):
        logging.error("RETRAINING - Base model not found!")
        return

    model = YOLO(WEIGHTS_PATH)

    # Обучаем (fine-tuning)
    model.train(
        data=yaml_path,
        epochs=10,  # Для дообучения достаточно 10 эпох
        imgsz=640,
        batch=2,  # Маленький батч для маленького датасета
        device=0,
        workers=0,
        name='retrain_run'
    )

    # Обновляем веса в рабочей папке
    new_weights = 'runs/detect/retrain_run/weights/best.pt'
    if os.path.exists(new_weights):
        shutil.copy(new_weights, WEIGHTS_PATH)
        logging.info("RETRAINING - Successfully updated production weights.")

    # Убираем данные в архив
    archive_used_data()

    # Удаляем временный yaml
    if os.path.exists(yaml_path):
        os.remove(yaml_path)

    print("Дообучение завершено! Модель обновлена.")


if __name__ == "__main__":
    run_continual_learning()