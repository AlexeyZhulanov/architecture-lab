import asyncio
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
import aiohttp

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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
async def handle_photo(message: types.Message):
    # Берем фото в лучшем качестве (последнее в списке)
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)

    # Скачиваем файл в память
    downloaded_file = await bot.download_file(file_info.file_path)

    # Пересылаем файл на Backend
    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData()
        data.add_field('file', downloaded_file, filename=f"user_{message.from_user.id}_{message.message_id}.jpg")

        # Делаем POST запрос к FastAPI
        url_with_user = f"{BACKEND_URL}/process-image/?user_id={message.from_user.id}"
        try:
            async with session.post(url_with_user, data=data) as response:
                resp_json = await response.json()
                await message.answer(resp_json.get("status", "Файл отправлен на сервер."))
        except Exception:
            await message.answer("Ошибка связи с вычислительным сервером.")


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
@dp.message(~F.photo & ~F.text & ~F.document)
async def handle_other(message: types.Message):
    await message.answer("Извините, я понимаю только фотографии. 📸")


async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())