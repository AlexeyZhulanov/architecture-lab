import asyncio
import os
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from middleware import AlbumMiddleware
from typing import List

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.message.middleware(AlbumMiddleware())

# URL FastAPI балансировщика (внутри Docker)
BACKEND_URL = "http://backend:8000"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я система детекции дефектов труб.\n\n"
        "Отправь мне фото, и я проверю его на трещины и засоры.\n"
        "Для просмотра статистики системы используй команду /stats"
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BACKEND_URL}/stats") as response:
                stats = await response.json()
                text = (
                    f"📊 <b>Статистика инфраструктуры:</b>\n\n"
                    f"📸 Обработано запросов: <b>{stats['total_requests']}</b>\n"
                    f"⏳ Ожидают дообучения: <b>{stats['pending_images']}</b> шт.\n"
                    f"🔄 Пройдено циклов дообучения: <b>{stats['retrain_cycles']}</b>\n"
                    f"⚠️ В карантине (ждут ручной разметки): <b>{stats['quarantine_items']}</b> шт."
                )
                await message.answer(text, parse_mode="HTML")
        except Exception:
            await message.answer("Ошибка связи с сервером статистики.")


@dp.message(F.photo)
async def handle_photo_batch(message: types.Message, album: List[types.Message] = None):
    # Определяем, пришел альбом или одно фото
    messages = album if album else [message]
    photos_count = len(messages)

    status_msg = await message.answer(f"⏳ Загружаю {photos_count} фото. Я пришлю результаты ТОЛЬКО для тех кадров, где будут обнаружены дефекты.")

    # Формируем multipart/form-data для пакетной отправки
    data = aiohttp.FormData()

    for msg in messages:
        # Берем фото в лучшем качестве (последнее в списке)
        photo = msg.photo[-1]
        file_info = await bot.get_file(photo.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        data.add_field('files', downloaded_file, filename=f"user_{message.from_user.id}_{msg.message_id}.jpg")

    # Делаем POST запрос к FastAPI
    url = f"{BACKEND_URL}/process-batch/?user_id={message.from_user.id}"

    # Пересылаем файл на Backend
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=data) as response:
                resp_json = await response.json()

                if resp_json.get("status") == "error":
                    final_text = f"❌ {resp_json.get('message')}"
                else:
                    final_text = f"✅ Пакет из {photos_count} фото в обработке. Ожидайте уведомлений о дефектах."

                await status_msg.edit_text(final_text)

        except Exception:
            await status_msg.edit_text("❌ Ошибка связи с вычислительным сервером.")


@dp.message(F.video)
async def handle_video(message: types.Message):
    status_msg = await message.answer("⏳ Скачиваю видео... Это может занять некоторое время.")

    # Получаем информацию о файле
    video = message.video

    # Базовая защита на стороне Telegram (20MB - стандартный лимит бота, если не поднят локальный сервер API)
    if video.file_size > 20 * 1024 * 1024:
        return await status_msg.edit_text("❌ Видео слишком большое! Telegram боты принимают файлы до 20 МБ.")

    file_info = await bot.get_file(video.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)

    # Формируем данные для отправки на Бэкенд
    data = aiohttp.FormData()
    data.add_field('file', downloaded_file, filename=f"video_{message.from_user.id}_{message.message_id}.mp4")

    url = f"{BACKEND_URL}/process-video/?user_id={message.from_user.id}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=data) as response:
                resp_json = await response.json()

                if resp_json.get("status") == "error":
                    await status_msg.edit_text(resp_json.get('message'))
                else:
                    await status_msg.edit_text("✅ Видео успешно загружено в очередь! Я пришлю скриншоты тех моментов, где обнаружу дефекты.")

        except Exception:
            await status_msg.edit_text("❌ Ошибка связи с вычислительным сервером.")


@dp.callback_query(F.data.contains("|"))
async def process_feedback(callback: types.CallbackQuery):
    status, file_id = callback.data.split("|")

    # Отправляем фидбек на бэкенд
    async with aiohttp.ClientSession() as session:
        await session.post(f"{BACKEND_URL}/feedback", json={"file_id": file_id, "status": status})

    # Редактируем сообщение, убирая кнопки
    if status == "confirm":
        text = "✅ Данные отправлены на дообучение."
    elif status == "reject":
        text = "❌ Записано как ложное срабатывание (будет использовано как Background Image)."
    else:
        text = "⚠️ Файл помещен в карантин для последующей ручной разметки."

    await callback.message.edit_caption(caption=text, reply_markup=None)
    await callback.answer()


# Если прислали картинку как "Документ/Файл"
@dp.message(F.document)
async def handle_document(message: types.Message):
    await message.answer("Пожалуйста, отправляйте изображения именно как «Фото», а не как файл. Так мне проще их обрабатывать!")

# Если прислали текст
@dp.message(F.text)
async def handle_text(message: types.Message):
    await message.answer(
        "Я — автоматизированная система компьютерного зрения. 🤖\n"
        "Я не умею поддерживать беседу. Моя задача — искать дефекты на трубах.\n\n"
        "Пожалуйста, отправьте мне фотографию или нажмите /stats."
    )

# Если прислали стикер, кружочек, аудио, локацию и т.д.
@dp.message(~F.photo & ~F.video & ~F.text & ~F.document)
async def handle_other(message: types.Message):
    await message.answer("Извините, я понимаю только фотографии. 📸")


async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())