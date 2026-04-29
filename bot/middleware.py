import asyncio
from typing import Any, Callable, Dict, List
from aiogram import BaseMiddleware
from aiogram.types import Message


class AlbumMiddleware(BaseMiddleware):
    """Мидлварь для группировки медиа-файлов (альбомов)"""

    def __init__(self, latency: float = 1.5):
        self.latency = latency
        self.album_data: Dict[str, List[Message]] = {}
        super().__init__()

    async def __call__(
            self,
            handler: Callable[[Message, Dict[str, Any]], Any],
            event: Message,
            data: Dict[str, Any]
    ) -> Any:
        # Если это не фото или не часть группы - обрабатываем как обычно
        if not event.media_group_id:
            return await handler(event, data)

        # Если группа уже создана, добавляем сообщение и выходим
        if event.media_group_id in self.album_data:
            self.album_data[event.media_group_id].append(event)
            return None

        # Если это первое сообщение группы - создаем список и запускаем таймер
        self.album_data[event.media_group_id] = [event]

        await asyncio.sleep(self.latency)  # Ждем остальные фото

        # Окно закрылось. Достаем все собранные сообщения
        messages = self.album_data.pop(event.media_group_id)
        data["album"] = messages  # Передаем список сообщений в хэндлер
        return await handler(event, data)
