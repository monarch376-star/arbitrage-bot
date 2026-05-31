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

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    logging.error("FATAL: TELEGRAM_TOKEN environment variable not set.")
    exit(1)

ADMIN_ID = int(os.environ.get("ADMIN_CHAT_ID", 2715781))
THRESHOLD_FILE = "threshold.txt"
DEFAULT_THRESHOLD = 0.5
SIGNAL_COOLDOWN = 60 * 60  # 1 час

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

refresh_btn = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh")]
])

price_history = defaultdict(list)
last_alert_time = {}
logging.basicConfig(level=logging.INFO)

def load_threshold():
    if os.path.exists(THRESHOLD_FILE):
        with open(THRESHOLD_FILE, 'r') as f:
            return float(f.read().strip())
    return DEFAULT_THRESHOLD

def save_threshold(value):
    with open(THRESHOLD_FILE, 'w') as f:
        f.write(str(value))

async def fetch_with_retry(session, url, retries=3):
    """Пытается получить данные с повторными попытками"""
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=15) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logging.warning(f"Attempt {attempt+1}: HTTP {response.status} for {url}")
        except asyncio.TimeoutError:
            logging.warning(f"Attempt {attempt+1}: Timeout for {url}")
        except Exception as e:
            logging.warning(f"Attempt {attempt+1}: {e} for {url}")
        
        if attempt < retries - 1:
            await asyncio.sleep(2)
    return None

async def get_prices():
    """Получает цены с Bybit и Bitget"""
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.resolver.AsyncResolver(nameservers=["8.8.8.8", "8.8.4.4"])
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        prices = {}
        
        # ---- Bybit ----
        bybit_url = "https://api.bybit.com/v5/market/tickers?category=linear"
        data = await fetch_with_retry(session, bybit_url)
        if data and data.get('retCode') == 0:
            for item in data['result']['list']:
                symbol = item['symbol']
                if symbol.endswith('USDT'):
                    coin = symbol.replace('USDT', '')
                    try:
                        prices[f"bybit_{coin}"] = float(item['lastPrice'])
                    except (ValueError, TypeError):
                        pass
        
        # ---- Bitget ----
        bitget_url = "https://api.bitget.com/api/v2/mix/market/tickers?productType=umcbl"
        data = await fetch_with_retry(session, bitget_url)
        if data and data.get('code') == '00000':
            for item in data['data']:
                symbol = item['symbol']
                if symbol.endswith('USDT'):
                    coin = symbol.replace('USDT', '')
                    try:
                        prices[f"bitget_{coin}"] = float(item['last'])
                    except (ValueError, TypeError):
                        pass
        
        logging.info(f"Получено цен: {len(prices)}")
        return prices

def analyze(prices):
    """Анализирует отклонения"""
    coins = defaultdict(dict)
    for key, price in prices.items():
        ex, coin = key.split('_', 1)
        coins[coin][ex] = price
    
    results = []
    now = datetime.now()
    threshold = load_threshold()
    
    for coin, exchanges in coins.items():
        if 'bybit' in exchanges and 'bitget' in exchanges:
            ratio = (exchanges['bybit'] / exchanges['bitget']) * 1000
            price_history[coin].append((now, ratio))
            cutoff = now - timedelta(hours=1)
            price_history[coin] = [(t, r) for t, r in price_history[coin] if t > cutoff]
            
            if len(price_history[coin]) >= 3:
                avg = sum(r for _, r in price_history[coin]) / len(price_history[coin])
                deviation = abs(ratio - avg) / avg * 100
                last = last_alert_time.get(coin)
                if deviation >= threshold and (last is None or (now - last) > timedelta(seconds=SIGNAL_COOLDOWN)):
                    results.append({
                        'coin': coin,
                        'deviation': deviation,
                        'ratio': ratio,
                        'avg': avg,
                        'bybit': exchanges['bybit'],
                        'bitget': exchanges['bitget'],
                        'time': now
                    })
    return results

# --- КОМАНДЫ ТЕЛЕГРАМА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        f"🤖 **Арбитражный бот (активный режим)**\n\n"
        f"Bybit ↔ Bitget\n"
        f"Порог: {load_threshold()}%\n\n"
        f"/status — проверить рынок\n"
        f"/threshold X — изменить порог\n\n"
        f"Бот работает 24/7. Сигналы будут приходить автоматически.",
        reply_markup=refresh_btn
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    await message.answer("🔍 Сканирую рынок...", reply_markup=refresh_btn)
    prices = await get_prices()
    if not prices:
        await message.answer("❌ Не удалось получить данные с бирж. Проверьте интернет.", reply_markup=refresh_btn)
        return
    
    results = analyze(prices)
    if not results:
        await message.answer(f"✅ Отклонений выше порога нет. (Проверено {len(prices)} цен)", reply_markup=refresh_btn)
    else:
        text = f"📊 **Найдено сигналов: {len(results)}**\n\n"
        for r in results[:5]:
            text += f"**{r['coin']}**: {r['deviation']:.2f}%\n"
            text += f"Bybit: {r['bybit']:.6f} | Bitget: {r['bitget']:.6f}\n\n"
        await message.answer(text, reply_markup=refresh_btn)

@dp.message(Command("threshold"))
async def cmd_threshold(message: types.Message):
    args = message.text.split()
    if len(args) == 1:
        await message.answer(f"🎯 Текущий порог: {load_threshold()}%", reply_markup=refresh_btn)
        return
    try:
        val = float(args[1])
        if val < 0.1 or val > 10:
            await message.answer("❌ От 0.1% до 10%", reply_markup=refresh_btn)
            return
        save_threshold(val)
        await message.answer(f"✅ Порог: {val}%", reply_markup=refresh_btn)
    except:
        await message.answer("❌ Используйте: `/threshold 0.7`", reply_markup=refresh_btn)

@dp.callback_query(lambda c: c.data == "refresh")
async def refresh_callback(callback: types.CallbackQuery):
    await cmd_status(callback.message)
    await callback.answer()

# --- ФОНОВОЕ СКАНИРОВАНИЕ ---
async def background_scanner():
    """Фоновое сканирование и отправка сигналов"""
    while True:
        try:
            logging.info("Фоновое сканирование...")
            prices = await get_prices()
            if prices:
                results = analyze(prices)
                for r in results:
                    text = (
                        f"⚠️ **СИГНАЛ** {r['deviation']:.2f}%\n\n"
                        f"{r['coin']}\nBybit: {r['bybit']:.6f}\n"
                        f"Bitget: {r['bitget']:.6f}\n"
                        f"Соотношение: {r['ratio']:.2f} (ср. {r['avg']:.2f})"
                    )
                    await bot.send_message(ADMIN_ID, text, reply_markup=refresh_btn)
                    last_alert_time[r['coin']] = r['time']
                    logging.info(f"Сигнал для {r['coin']}: {r['deviation']:.2f}%")
        except Exception as e:
            logging.error(f"Ошибка в фоне: {e}")
        await asyncio.sleep(60)  # Проверка раз в минуту

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def health_check(request):
    return web.Response(text="Bot is running")

async def on_startup(app):
    asyncio.create_task(background_scanner())
    logging.info("Бот запущен, фоновый сканер активен")

async def main():
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.on_startup.append(on_startup)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Бот запущен на Render, порт {port}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
