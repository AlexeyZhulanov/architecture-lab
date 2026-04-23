import os
import shutil
import yaml
import random
import logging
from ultralytics import YOLO
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    filename='/app/logs/system_load.log',
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)

# Пути внутри Docker-контейнера
STAGING_DIR = '/app/data/new_data'
STAGING_IMG = os.path.join(STAGING_DIR, 'images')
STAGING_LBL = os.path.join(STAGING_DIR, 'labels')

TEMP_TRAIN_DIR = '/app/data/temp_train' # Временная папка для правильного разбиения
WEIGHTS_PATH = '/app/data/weights/yolov8s_pipe.pt'
ARCHIVE_DIR = '/app/data/archive'

# Порог для старта дообучения
THRESHOLD = 5


def create_yaml_for_training(base_path):
    """Создает временный data.yaml для новых данных"""
    yaml_path = os.path.join(base_path, 'retrain_data.yaml')
    data = {
        'path': base_path,
        'train': 'train/images',
        'val': 'val/images',
        'names': {
            0: 'buckling',
            1: 'crack',
            2: 'debris',
            3: 'hole',
            4: 'jntoffs',
            5: 'obsc',
            6: 'utits'
        }
    }
    with open(yaml_path, 'w') as f:
        yaml.dump(data, f)
    return yaml_path


def prepare_split_data(images_list):
    """Создает структуру train/val и распределяет файлы 80/20"""
    # Создаем структуру папок
    for split in ['train', 'val']:
        os.makedirs(os.path.join(TEMP_TRAIN_DIR, split, 'images'), exist_ok=True)
        os.makedirs(os.path.join(TEMP_TRAIN_DIR, split, 'labels'), exist_ok=True)

    # Перемешиваем список для случайного разбиения
    random.shuffle(images_list)

    # Считаем сколько файлов пойдет в валидацию (минимум 1, либо 20%)
    val_count = max(1, int(len(images_list) * 0.2))

    val_files = images_list[:val_count]
    train_files = images_list[val_count:]

    def copy_files(file_list, split_name):
        for img_name in file_list:
            lbl_name = img_name.rsplit('.', 1)[0] + '.txt'

            src_img = os.path.join(STAGING_IMG, img_name)
            src_lbl = os.path.join(STAGING_LBL, lbl_name)

            dst_img = os.path.join(TEMP_TRAIN_DIR, split_name, 'images', img_name)
            dst_lbl = os.path.join(TEMP_TRAIN_DIR, split_name, 'labels', lbl_name)

            shutil.copy(src_img, dst_img)
            if os.path.exists(src_lbl):
                shutil.copy(src_lbl, dst_lbl)

    copy_files(train_files, 'train')
    copy_files(val_files, 'val')

    logging.info(f"RETRAINING - Data split: {len(train_files)} train, {len(val_files)} val.")


def archive_and_cleanup():
    """Архивирует исходники из new_data и удаляет временную папку temp_train"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_folder = os.path.join(ARCHIVE_DIR, f"batch_{timestamp}")
    os.makedirs(archive_folder, exist_ok=True)

    # Переносим оригиналы в архив
    for folder in ['images', 'labels']:
        src = os.path.join(STAGING_DIR, folder)
        dst = os.path.join(archive_folder, folder)
        shutil.copytree(src, dst)

        # Очищаем оригинальную папку
        for file in os.listdir(src):
            os.remove(os.path.join(src, file))

    # Удаляем временную папку для обучения
    if os.path.exists(TEMP_TRAIN_DIR):
        shutil.rmtree(TEMP_TRAIN_DIR)


def run_continual_learning():
    if not os.path.exists(STAGING_IMG) or not os.path.exists(STAGING_LBL):
        return

    # Считаем количество новых изображений
    images = [f for f in os.listdir(STAGING_IMG) if f.endswith(('.jpg', '.png'))]

    if len(images) < THRESHOLD:
        print(f"Недостаточно данных для дообучения. Текущее количество: {len(images)}/{THRESHOLD}")
        return

    logging.info(f"RETRAINING - Started auto-retraining on {len(images)} new images.")
    print(f"Начинаем дообучение на {len(images)} новых файлах...")

    # Готовим данные (сплит train/val)
    prepare_split_data(images)

    # Создаем конфиг в корне временной папки
    yaml_path = create_yaml_for_training(TEMP_TRAIN_DIR)

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
        project='runs/detect',
        name='retrain_run',
        exist_ok=True  # Перезапись папки при следующих дообучениях
    )

    # Обновляем веса в рабочей папке
    new_weights = 'runs/detect/retrain_run/weights/best.pt'
    if os.path.exists(new_weights):
        shutil.copy(new_weights, WEIGHTS_PATH)
        logging.info("RETRAINING - Successfully updated production weights.")

    # Убираем данные в архив
    archive_and_cleanup()

    print("Дообучение завершено! Модель обновлена.")


if __name__ == "__main__":
    run_continual_learning()