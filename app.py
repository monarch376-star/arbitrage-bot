import asyncio
import os
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    logging.error("Нет токена")
    exit(1)

ADMIN_ID = int(os.environ.get("ADMIN_CHAT_ID", 2715781))

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# --- ПРОСТЕЙШАЯ ПРОВЕРКА БИРЖ ---
async def check_bybit():
    """Проверяет доступность Bybit и получает цену BTC"""
    url = "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT"
    
    # Пробуем разные DNS
    nameservers = [
        ["8.8.8.8", "8.8.4.4"],  # Google DNS
        ["1.1.1.1", "1.0.0.1"],  # Cloudflare DNS
        None  # системный DNS
    ]
    
    for ns in nameservers:
        try:
            if ns:
                connector = aiohttp.TCPConnector(
                    resolver=aiohttp.resolver.AsyncResolver(nameservers=ns)
                )
            else:
                connector = aiohttp.TCPConnector()
            
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('retCode') == 0:
                            price = data['result']['list'][0]['lastPrice']
                            return f"✅ Успех! Цена BTC: {price} USD (DNS: {ns[0] if ns else 'system'})"
                    else:
                        return f"❌ Ошибка HTTP {response.status} (DNS: {ns[0] if ns else 'system'})"
        except asyncio.TimeoutError:
            logging.warning(f"Timeout with DNS {ns}")
        except Exception as e:
            logging.warning(f"Error with DNS {ns}: {e}")
    
    return "❌ Не удалось подключиться к Bybit ни через один DNS"

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("🤖 Тестовый бот на Render. Отправь /bybit для проверки соединения.")

@dp.message(Command("bybit"))
@dp.message(Command("status"))
async def check(message: types.Message):
    await message.answer("🔍 Проверяю соединение с Bybit...")
    result = await check_bybit()
    await message.answer(result)

async def main():
    app = web.Application()
    app.router.add_get('/health', lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Тестовый бот запущен на порту {port}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
