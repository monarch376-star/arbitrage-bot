import asyncio
import os
import logging
from datetime import datetime, timedelta
from collections import defaultdict

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    logging.error("Нет токена")
    exit(1)

ADMIN_ID = int(os.environ.get("ADMIN_CHAT_ID", 2715781))
THRESHOLD_FILE = "threshold.txt"
DEFAULT_THRESHOLD = 0.5
SIGNAL_COOLDOWN = 60 * 60

bot = Bot(token=TOKEN)
dp = Dispatcher()
refresh_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh")]])
price_history = defaultdict(list)
last_alert_time = {}

def load_threshold():
    if os.path.exists(THRESHOLD_FILE):
        with open(THRESHOLD_FILE, 'r') as f:
            return float(f.read().strip())
    return DEFAULT_THRESHOLD

async def get_prices():
    connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.AsyncResolver(nameservers=["8.8.8.8"]))
    async with aiohttp.ClientSession(connector=connector) as session:
        prices = {}
        try:
            async with session.get("https://api.bybit.com/v5/market/tickers?category=linear") as resp:
                data = await resp.json()
                if data['retCode'] == 0:
                    for item in data['result']['list']:
                        if item['symbol'].endswith('USDT'):
                            coin = item['symbol'].replace('USDT', '')
                            prices[f"bybit_{coin}"] = float(item['lastPrice'])
        except Exception as e:
            print(f"Bybit error: {e}")
        try:
            async with session.get("https://api.bitget.com/api/v2/mix/market/tickers?productType=umcbl") as resp:
                data = await resp.json()
                if data['code'] == '00000':
                    for item in data['data']:
                        if item['symbol'].endswith('USDT'):
                            coin = item['symbol'].replace('USDT', '')
                            prices[f"bitget_{coin}"] = float(item['last'])
        except Exception as e:
            print(f"Bitget error: {e}")
        return prices

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(f"🤖 Бот запущен!\nПорог: {load_threshold()}%\n/status - проверить")

@dp.message(Command("status"))
async def status(message: types.Message):
    await message.answer("🔍 Сканирую...")
    prices = await get_prices()
    if not prices:
        await message.answer("❌ Нет данных с бирж")
        return
    await message.answer(f"✅ Найдено {len(prices)} цен")

async def main():
    app = web.Application()
    app.router.add_get('/health', lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print("Бот запущен на Render!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
