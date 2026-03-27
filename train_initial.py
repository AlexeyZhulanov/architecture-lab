from ultralytics import YOLO
import os


def main():
    print("Начинаем первичное обучение YOLOv8s...")

    # 1. Загружаем базовую, "пустую" модель (скачается автоматически)
    model = YOLO('yolov8s.pt')

    # 2. Указываем путь к файлу data.yaml нашего скачанного датасета
    dataset_yaml = os.path.abspath("data/dataset/data.yaml")

    # 3. Запускаем обучение
    results = model.train(
        data=dataset_yaml,
        epochs=50,  # 50 эпох для начала хватит, чтобы не ждать сутки
        imgsz=640,  # Стандартный размер картинок для YOLO
        batch=16,  # Размер батча (если будет ошибка памяти, нужно снизить до 8)
        name='pipe_model',  # Имя папки с результатами
        device=0,  # 0 означает использовать видеокарту (GPU)
        workers=0  # На Windows не работает, поэтому 0
    )

    print("Обучение завершено! Веса сохранены в папке runs/detect/pipe_model/weights/best.pt")


if __name__ == '__main__':
    main()