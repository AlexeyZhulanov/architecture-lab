import asyncio
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
import aiohttp

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# URL FastAPI балансировщика (внутри Docker)
BACKEND_URL = "http://backend:8000/process-image/"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я система детекции дефектов труб. Отправь мне фото, и я проверю его на трещины и засоры.")


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
        url_with_user = f"{BACKEND_URL}?user_id={message.from_user.id}"
        try:
            async with session.post(url_with_user, data=data) as response:
                resp_json = await response.json()
                await message.answer(resp_json.get("status", "Файл отправлен на сервер."))
        except Exception:
            await message.answer("Ошибка связи с вычислительным сервером.")


@dp.callback_query(F.data.startswith("confirm") | F.data.startswith("reject"))
async def process_feedback(callback: types.CallbackQuery):
    action, file_id = callback.data.split("|")
    confirmed = (action == "confirm")

    # Отправляем фидбек на бэкенд
    async with aiohttp.ClientSession() as session:
        await session.post("http://backend:8000/feedback", json={"file_id": file_id, "confirmed": confirmed})

    # Редактируем сообщение, убирая кнопки
    text = "✅ Спасибо! Данные будут использованы для улучшения модели." if confirmed else "❌ Понял, ложное срабатывание."
    await callback.message.edit_caption(caption=text, reply_markup=None)
    await callback.answer()


async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())