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

async def fetch_json(session, url):
    try:
        async with session.get(url, timeout=15) as response:
            if response.status == 200:
                return await response.json()
            else:
                logging.warning(f"HTTP {response.status} for {url}")
                return None
    except Exception as e:
        logging.warning(f"Error for {url}: {e}")
        return None

async def get_prices():
    """Получает цены с Bybit, Bitget, OKX, Gate"""
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.resolver.AsyncResolver(nameservers=["8.8.8.8", "8.8.4.4"])
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        prices = {}
        
        # ---- Bybit ----
        data = await fetch_json(session, "https://api.bybit.com/v5/market/tickers?category=linear")
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
        data = await fetch_json(session, "https://api.bitget.com/api/v2/mix/market/tickers?productType=umcbl")
        if data and data.get('code') == '00000':
            for item in data['data']:
                symbol = item['symbol']
                if symbol.endswith('USDT'):
                    coin = symbol.replace('USDT', '')
                    try:
                        prices[f"bitget_{coin}"] = float(item['last'])
                    except (ValueError, TypeError):
                        pass
        
        # ---- OKX (свопы) ----
        data = await fetch_json(session, "https://www.okx.com/api/v5/market/tickers?instType=SWAP")
        if data and data.get('code') == '0':
            for item in data['data']:
                symbol = item.get('instId', '')
                if symbol.endswith('-USDT-SWAP'):
                    coin = symbol.replace('-USDT-SWAP', '')
                    try:
                        prices[f"okx_{coin}"] = float(item['last'])
                    except (ValueError, TypeError):
                        pass
        
        # ---- Gate (фьючерсы) ----
        data = await fetch_json(session, "https://api.gateio.ws/api/v4/futures/usdt/tickers")
        if data and isinstance(data, list):
            for item in data:
                symbol = item.get('contract', '')
                if symbol.endswith('_USDT'):
                    coin = symbol.replace('_USDT', '')
                    try:
                        prices[f"gate_{coin}"] = float(item['last'])
                    except (ValueError, TypeError):
                        pass
        
        logging.info(f"Получено цен: {len(prices)}")
        return prices

def analyze(prices):
    """Анализирует отклонения между всеми парами бирж"""
    coins = defaultdict(dict)
    for key, price in prices.items():
        ex, coin = key.split('_', 1)
        coins[coin][ex] = price
    
    results = []
    now = datetime.now()
    threshold = load_threshold()
    
    for coin, exchanges in coins.items():
        if len(exchanges) < 2:
            continue
        
        # Берём первую пару бирж для анализа (можно расширить)
        ex_list = list(exchanges.keys())
        for i in range(len(ex_list)):
            for j in range(i+1, len(ex_list)):
                ex1, ex2 = ex_list[i], ex_list[j]
                price1, price2 = exchanges[ex1], exchanges[ex2]
                if price1 and price2 and price1 > 0 and price2 > 0:
                    ratio = (price1 / price2) * 1000
                    key = f"{coin}_{ex1}_{ex2}"
                    price_history[key].append((now, ratio))
                    cutoff = now - timedelta(hours=1)
                    price_history[key] = [(t, r) for t, r in price_history[key] if t > cutoff]
                    
                    if len(price_history[key]) >= 3:
                        avg = sum(r for _, r in price_history[key]) / len(price_history[key])
                        deviation = abs(ratio - avg) / avg * 100
                        last = last_alert_time.get(key)
                        if deviation >= threshold and (last is None or (now - last) > timedelta(seconds=SIGNAL_COOLDOWN)):
                            results.append({
                                'coin': coin,
                                'deviation': deviation,
                                'ex1': ex1,
                                'ex2': ex2,
                                'price1': price1,
                                'price2': price2,
                                'ratio': ratio,
                                'avg': avg,
                                'time': now
                            })
    return results

# --- КОМАНДЫ ТЕЛЕГРАМА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        f"🤖 **Арбитражный бот (4 биржи)**\n\n"
        f"Биржи: Bybit, Bitget, OKX, Gate\n"
        f"Порог: {load_threshold()}%\n\n"
        f"/status — проверить рынок\n"
        f"/threshold X — изменить порог",
        reply_markup=refresh_btn
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    await message.answer("🔍 Сканирую рынок...", reply_markup=refresh_btn)
    prices = await get_prices()
    if not prices:
        await message.answer("❌ Не удалось получить данные с бирж.", reply_markup=refresh_btn)
        return
    
    results = analyze(prices)
    if not results:
        await message.answer(f"✅ Отклонений выше порога нет. (Проверено {len(prices)} цен)", reply_markup=refresh_btn)
    else:
        text = f"📊 **Найдено сигналов: {len(results)}**\n\n"
        for r in results[:5]:
            text += f"**{r['coin']}**: {r['deviation']:.2f}%\n"
            text += f"{r['ex1'].upper()}: {r['price1']:.6f} | {r['ex2'].upper()}: {r['price2']:.6f}\n\n"
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
    while True:
        try:
            prices = await get_prices()
            if prices:
                results = analyze(prices)
                for r in results:
                    text = (
                        f"⚠️ **СИГНАЛ** {r['deviation']:.2f}%\n\n"
                        f"{r['coin']}\n"
                        f"{r['ex1'].upper()}: {r['price1']:.6f}\n"
                        f"{r['ex2'].upper()}: {r['price2']:.6f}\n"
                        f"Соотношение: {r['ratio']:.2f} (ср. {r['avg']:.2f})"
                    )
                    await bot.send_message(ADMIN_ID, text, reply_markup=refresh_btn)
                    last_alert_time[f"{r['coin']}_{r['ex1']}_{r['ex2']}"] = r['time']
                    logging.info(f"Сигнал: {r['coin']} ({r['ex1']} ↔ {r['ex2']}) - {r['deviation']:.2f}%")
        except Exception as e:
            logging.error(f"Ошибка в фоне: {e}")
        await asyncio.sleep(60)

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
