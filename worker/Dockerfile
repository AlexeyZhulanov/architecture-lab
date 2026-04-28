FROM python:3.10-slim

WORKDIR /app

# Обновляем пакетный менеджер и ставим системные зависимости для OpenCV
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# СНАЧАЛА ставим PyTorch с поддержкой CUDA (специальный индекс пакетов)
# Это гарантирует, что модель увидит проброшенную видеокарту
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# Затем ставим YOLOv8 (ultralytics) и Celery из нашего файла
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Запускаем Celery worker.
# --concurrency=1 означает, что 1 воркер берет строго 1 картинку за раз.
# Это защита видеокарты от переполнения памяти (OOM - Out of Memory).
CMD ["celery", "-A", "tasks", "worker", "--beat", "--loglevel=info", "--concurrency=1"]