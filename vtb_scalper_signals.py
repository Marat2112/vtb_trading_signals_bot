import os
import datetime
import time
import logging
import asyncio
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from tinkoff.invest import Client, CandleInterval, SecurityTradingStatus
from io import BytesIO

# Конфигурация
TOKEN = "your_token_invest_api"
TELEGRAM_TOKEN = "your_noken_telegram_bot"
TELEGRAM_CHAT_ID = "your_id_chat"
FIGI = "BBG004730ZJ9"  # FIGI акции ВТБ
TRADE_INTERVAL = CandleInterval.CANDLE_INTERVAL_1_MIN
SHORT_MA_PERIOD = 5
LONG_MA_PERIOD = 20
HISTORY_DAYS = 1
SIGNAL_CONFIRMATION = 3  # Количество подтверждающих сигналов

# Глобальные переменные состояния
current_position = 0  # 0 - нет позиции, 1 - куплено
entry_price = 0.0
entry_time = None
signals_history = []
bot_instance = None

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('VTBSignalSystem')

# ================== Telegram Bot Functions ================== #
async def telegram_send_message(text, reply_markup=None):
    """Отправка сообщения в Telegram"""
    try:
        await bot_instance.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        logger.info(f"Telegram: {text}")
    except Exception as e:
        logger.error(f"Ошибка Telegram: {str(e)}")

async def telegram_send_photo(photo, caption=None):
    """Отправка изображения в Telegram"""
    try:
        await bot_instance.send_photo(
            chat_id=TELEGRAM_CHAT_ID,
            photo=photo,
            caption=caption,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {str(e)}")

def create_signal_keyboard():
    """Создание клавиатуры для управления позицией"""
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить покупку", callback_data='confirm_buy')],
        [InlineKeyboardButton("❌ Отменить сигнал", callback_data='cancel_signal')],
        [InlineKeyboardButton("📊 Показать график", callback_data='show_chart')]
    ]
    if current_position == 1:
        keyboard[0][0] = InlineKeyboardButton("✅ Подтвердить продажу", callback_data='confirm_sell')
        keyboard.append([InlineKeyboardButton("⚡ Экстренная продажа", callback_data='emergency_sell')])
    
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    await update.message.reply_text(
        "🚀 Система сигналов для торговли акциями ВТБ активирована!\n"
        "Используйте команды:\n"
        "/status - текущий статус\n"
        "/chart - текущий график\n"
        "/position - управление позицией"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /status"""
    status = "🟢 Активна" if len(signals_history) > 0 else "🟡 Ожидание данных"
    position_status = "Куплено" if current_position == 1 else "Нет позиции"
    
    message = (
        f"📊 *Статус системы*\n"
        f"• Система: {status}\n"
        f"• Позиция: {position_status}\n"
        f"• Акция: ВТБ\n"
        f"• FIGI: `{FIGI}`\n"
        f"• Последних сигналов: {len(signals_history)}"
    )
    
    if current_position == 1:
        current_price = await get_current_price()
        profit = (current_price - entry_price) / entry_price * 100
        hold_time = (datetime.datetime.now() - entry_time).total_seconds() / 60
        
        message += (
            f"\n\n💰 *Текущая позиция*\n"
            f"• Цена входа: {entry_price:.2f} RUB\n"
            f"• Время входа: {entry_time.strftime('%Y-%m-%d %H:%M')}\n"
            f"• Текущая цена: {current_price:.2f} RUB\n"
            f"• Прибыль: {profit:+.2f}%\n"
            f"• Время удержания: {hold_time:.1f} мин"
        )
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /chart"""
    try:
        with Client(TOKEN) as client:
            df = get_historical_candles(client, HISTORY_DAYS)
            if df.empty:
                await update.message.reply_text("⚠️ Недостаточно данных для построения графика")
                return
            
            fig = generate_chart(df)
            
            # Сохраняем график в буфер
            buf = BytesIO()
            fig.savefig(buf, format='png', dpi=100)
            buf.seek(0)
            
            await update.message.reply_photo(
                photo=buf,
                caption="📈 Текущий график ВТБ с индикаторами",
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Ошибка при построении графика: {str(e)}", exc_info=True)
        await update.message.reply_text(f"⚠️ Ошибка при построении графика: {str(e)}")

async def position_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /position"""
    keyboard = []
    
    if current_position == 0:
        keyboard.append([InlineKeyboardButton("📈 Сигнал на покупку", callback_data='force_buy')])
    else:
        keyboard.append([InlineKeyboardButton("📉 Сигнал на продажу", callback_data='force_sell')])
        keyboard.append([InlineKeyboardButton("🔥 Экстренная продажа", callback_data='emergency_sell')])
    
    keyboard.append([InlineKeyboardButton("🔄 Обновить статус", callback_data='refresh_status')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚙️ Управление позицией:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий кнопок"""
    query = update.callback_query
    await query.answer()
    
    global current_position, entry_price, entry_time
    
    if query.data == 'confirm_buy' and current_position == 0:
        current_price = await get_current_price()
        current_position = 1
        entry_price = current_price
        entry_time = datetime.datetime.now()
        
        await query.edit_message_text(
            f"✅ *Позиция открыта!*\n"
            f"• Активирована покупка ВТБ\n"
            f"• Цена: {current_price:.2f} RUB\n"
            f"• Время: {entry_time.strftime('%Y-%m-%d %H:%M')}\n"
            f"• Следующий сигнал на продажу будет автоматически проанализирован",
            parse_mode='Markdown'
        )
        
        # Отправляем рекомендацию по стоп-лоссу
        stop_loss_price = current_price * 0.97
        take_profit_price = current_price * 1.03
        
        await telegram_send_message(
            f"⚡ *Рекомендация по управлению рисками*\n"
            f"Установите ордера для защиты позиции:\n"
            f"• Стоп-лосс: `{stop_loss_price:.2f} RUB` (-3%)\n"
            f"• Тейк-профит: `{take_profit_price:.2f} RUB` (+3%)\n"
            f"\n"
            f"Изменить позицию: /position"
        )
    
    elif query.data == 'confirm_sell' and current_position == 1:
        current_price = await get_current_price()
        profit = (current_price - entry_price) / entry_price * 100
        hold_time = (datetime.datetime.now() - entry_time).total_seconds() / 60
        
        current_position = 0
        entry_price = 0.0
        
        await query.edit_message_text(
            f"✅ *Позиция закрыта!*\n"
            f"• Активирована продажа ВТБ\n"
            f"• Цена: {current_price:.2f} RUB\n"
            f"• Прибыль: {profit:+.2f}%\n"
            f"• Время удержания: {hold_time:.1f} мин",
            parse_mode='Markdown'
        )
    
    elif query.data == 'cancel_signal':
        await query.edit_message_text("❌ Сигнал отменен")
    
    elif query.data == 'show_chart':
        try:
            with Client(TOKEN) as client:
                df = get_historical_candles(client, HISTORY_DAYS)
                if df.empty:
                    await query.message.reply_text("⚠️ Недостаточно данных для построения графика")
                    return
                
                fig = generate_chart(df)
                
                # Сохраняем график в буфер
                buf = BytesIO()
                fig.savefig(buf, format='png', dpi=100)
                buf.seek(0)
                
                await query.message.reply_photo(
                    photo=buf,
                    caption="📈 Текущий график ВТБ с индикаторами",
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"Ошибка при построении графика: {str(e)}", exc_info=True)
            await query.message.reply_text(f"⚠️ Ошибка при построении графика: {str(e)}")
    
    elif query.data == 'force_buy':
        current_price = await get_current_price()
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить покупку", callback_data='confirm_buy')],
            [InlineKeyboardButton("❌ Отменить", callback_data='cancel_signal')]
        ])
        
        await query.edit_message_text(
            f"⚠️ *Ручной сигнал на покупку*\n"
            f"• Текущая цена: {current_price:.2f} RUB\n"
            f"• Рекомендуется покупка ВТБ\n"
            f"• Подтвердите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif query.data == 'force_sell':
        current_price = await get_current_price()
        profit = (current_price - entry_price) / entry_price * 100
        
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить продажу", callback_data='confirm_sell')],
            [InlineKeyboardButton("❌ Отменить", callback_data='cancel_signal')]
        ])
        
        await query.edit_message_text(
            f"⚠️ *Ручной сигнал на продажу*\n"
            f"• Текущая цена: {current_price:.2f} RUB\n"
            f"• Прибыль: {profit:+.2f}%\n"
            f"• Рекомендуется продажа ВТБ\n"
            f"• Подтвердите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif query.data == 'emergency_sell' and current_position == 1:
        current_price = await get_current_price()
        profit = (current_price - entry_price) / entry_price * 100
        
        current_position = 0
        entry_price = 0.0
        
        await query.edit_message_text(
            f"🚨 *Экстренная продажа!*\n"
            f"• Позиция принудительно закрыта\n"
            f"• Цена: {current_price:.2f} RUB\n"
            f"• Прибыль: {profit:+.2f}%",
            parse_mode='Markdown'
        )
    
    elif query.data == 'refresh_status':
        await status_command(update, context)

# ================== Trading Signal Functions ================== #
def get_historical_candles(client, days):
    """Получение исторических данных"""
    now_time = datetime.datetime.utcnow()
    from_time = now_time - datetime.timedelta(days=days)
    
    candles = client.get_all_candles(
        figi=FIGI,
        from_=from_time,
        to=now_time,
        interval=TRADE_INTERVAL
    )
    
    data = []
    for c in candles:
        data.append({
            'time': c.time,
            'open': c.open.units + c.open.nano / 1e9,
            'close': c.close.units + c.close.nano / 1e9,
            'high': c.high.units + c.high.nano / 1e9,
            'low': c.low.units + c.low.nano / 1e9,
            'volume': c.volume
        })
    
    df = pd.DataFrame(data)
    if df.empty:
        return df
        
    df['time'] = pd.to_datetime(df['time'])
    df = df.set_index('time')
    return df

def calculate_indicators(df):
    """Расчет индикаторов"""
    if len(df) < LONG_MA_PERIOD:
        return df
    
    # Рассчитываем индикаторы только если достаточно данных
    df['short_ma'] = df['close'].rolling(SHORT_MA_PERIOD).mean()
    df['long_ma'] = df['close'].rolling(LONG_MA_PERIOD).mean()
    
    # Определение сигналов
    df['signal'] = 0
    df.loc[df['short_ma'] > df['long_ma'], 'signal'] = 1  # Покупка
    df.loc[df['short_ma'] < df['long_ma'], 'signal'] = -1  # Продажа
    
    return df

async def get_current_price():
    """Текущая цена инструмента"""
    try:
        with Client(TOKEN) as client:
            last_price = client.market_data.get_last_prices(figi=[FIGI]).last_prices[0]
            return last_price.price.units + last_price.price.nano / 1e9
    except Exception as e:
        logger.error(f"Ошибка получения цены: {str(e)}")
        return 0.0

def generate_chart(df):
    """Генерация графика с индикаторами"""
    plt.figure(figsize=(12, 8))
    
    # График цены
    plt.subplot(2, 1, 1)
    plt.plot(df.index, df['close'], label='Цена ВТБ', color='blue')
    
    # Добавляем индикаторы только если они есть в данных
    if 'short_ma' in df.columns:
        plt.plot(df.index, df['short_ma'], label=f'MA {SHORT_MA_PERIOD}', color='orange', linestyle='--')
    
    if 'long_ma' in df.columns:
        plt.plot(df.index, df['long_ma'], label=f'MA {LONG_MA_PERIOD}', color='green', linestyle='-.')
    
    # Пометки сигналов (если есть данные)
    if 'signal' in df.columns:
        buy_signals = df[df['signal'] == 1]
        sell_signals = df[df['signal'] == -1]
        
        if not buy_signals.empty:
            plt.scatter(buy_signals.index, buy_signals['close'], marker='^', color='g', s=100, label='Сигнал покупки')
        if not sell_signals.empty:
            plt.scatter(sell_signals.index, sell_signals['close'], marker='v', color='r', s=100, label='Сигнал продажи')
    
    plt.title('График ВТБ с торговыми сигналами')
    plt.ylabel('Цена (RUB)')
    plt.legend()
    plt.grid(True)
    
    # Форматирование даты
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    plt.gca().xaxis.set_major_locator(mdates.HourLocator(interval=1))
    
    # График объема
    plt.subplot(2, 1, 2)
    plt.bar(df.index, df['volume'], color='blue', alpha=0.3)
    plt.title('Объем торгов')
    plt.ylabel('Объем')
    plt.grid(True)
    
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    plt.gca().xaxis.set_major_locator(mdates.HourLocator(interval=1))
    
    plt.tight_layout()
    return plt

def analyze_signals():
    """Анализ сигналов для принятия решения"""
    global signals_history
    
    if len(signals_history) < SIGNAL_CONFIRMATION:
        return None
    
    # Анализ последних сигналов
    last_signals = signals_history[-SIGNAL_CONFIRMATION:]
    
    # Проверка на устойчивый сигнал покупки
    if all(s == 1 for s in last_signals):
        return "BUY"
    
    # Проверка на устойчивый сигнал продажи
    if all(s == -1 for s in last_signals):
        return "SELL"
    
    return None

async def send_signal_notification(signal_type, current_price, df):
    """Отправка уведомления о сигнале"""
    if signal_type == "BUY":
        action = "ПОКУПКА"
        reason = "устойчивый восходящий тренд"
        recommendation = (
            "Рекомендуется открыть позицию в ближайшие 2-5 минут.\n"
            "Оптимальная цена входа: на 0.1-0.3% ниже текущей."
        )
    else:
        action = "ПРОДАЖА"
        reason = "устойчивый нисходящий тренд"
        recommendation = (
            "Рекомендуется закрыть позицию в ближайшие 2-5 минут.\n"
            "Оптимальная цена выхода: на 0.1-0.3% выше текущей."
        )
    
    # Генерация графика
    fig = generate_chart(df)
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    
    # Формирование сообщения
    message = (
        f"🚨 *СИГНАЛ {action} ВТБ*\n"
        f"• Текущая цена: `{current_price:.2f} RUB`\n"
        f"• Причина: {reason}\n"
        f"• Подтверждающих сигналов: {SIGNAL_CONFIRMATION}\n\n"
        f"📌 *Рекомендация*\n"
        f"{recommendation}"
    )
    
    # Отправка сообщения с графиком
    await telegram_send_photo(buf, caption=message)
    
    # Отправка клавиатуры для подтверждения действия
    reply_markup = create_signal_keyboard()
    await telegram_send_message("Подтвердите действие:", reply_markup)

async def check_position_health():
    """Проверка текущей позиции на предмет рисков"""
    if current_position != 1:
        return
    
    current_price = await get_current_price()
    profit = (current_price - entry_price) / entry_price * 100
    hold_time = (datetime.datetime.now() - entry_time).total_seconds() / 60
    
    # Критический стоп-лосс
    if profit < -5:
        await telegram_send_message(
            f"🚨 *КРИТИЧЕСКИЙ УБЫТОК!*\n"
            f"• Текущая цена: `{current_price:.2f} RUB`\n"
            f"• Убыток: `{profit:.2f}%`\n"
            f"• Рекомендуется немедленная продажа!",
            reply_markup=create_signal_keyboard()
        )
    # Предупреждение о стоп-лоссе
    elif profit < -3:
        await telegram_send_message(
            f"⚠️ *ПРЕДУПРЕЖДЕНИЕ О СТОП-ЛОССЕ*\n"
            f"• Текущая цена: `{current_price:.2f} RUB`\n"
            f"• Убыток: `{profit:.2f}%`\n"
            f"• Рассмотрите возможность продажи"
        )
    # Предупреждение о тейк-профите
    elif profit > 5:
        await telegram_send_message(
            f"⚠️ *ПРЕДУПРЕЖДЕНИЕ О ТЕЙК-ПРОФИТЕ*\n"
            f"• Текущая цена: `{current_price:.2f} RUB`\n"
            f"• Прибыль: `{profit:.2f}%`\n"
            f"• Рассмотрите возможность фиксации прибыли",
            reply_markup=create_signal_keyboard()
        )
    # Статус позиции (каждые 30 мин)
    elif hold_time > 30 and int(hold_time) % 30 == 0:
        await telegram_send_message(
            f"ℹ️ *СТАТУС ПОЗИЦИИ*\n"
            f"• Текущая цена: `{current_price:.2f} RUB`\n"
            f"• Прибыль: `{profit:.2f}%`\n"
            f"• Время удержания: `{hold_time:.1f} мин`"
        )

# ================== Main Trading Loop ================== #
async def signal_monitoring():
    """Основной цикл мониторинга сигналов"""
    global signals_history, bot_instance
    
    # Инициализация бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('status', status_command))
    application.add_handler(CommandHandler('chart', chart_command))
    application.add_handler(CommandHandler('position', position_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    bot_instance = application.bot
    await telegram_send_message("🚀 Система сигналов ВТБ активирована! Ожидание данных...")
    
    try:
        while True:
            try:
                with Client(TOKEN) as client:
                    # Получение и анализ данных
                    df = get_historical_candles(client, HISTORY_DAYS)
                    if df.empty or len(df) < max(SHORT_MA_PERIOD, LONG_MA_PERIOD):
                        await asyncio.sleep(30)
                        continue
                    
                    # Рассчитываем индикаторы
                    df = calculate_indicators(df)
                    
                    # Получение последнего сигнала
                    last_signal = df.iloc[-1]['signal']
                    current_price = df.iloc[-1]['close']
                    
                    # Сохранение сигнала в историю
                    signals_history.append(last_signal)
                    
                    # Анализ сигналов
                    decision = analyze_signals()
                    
                    # Отправка уведомления при наличии решения
                    if decision:
                        await send_signal_notification(decision, current_price, df)
                        # Очистка истории после отправки сигнала
                        signals_history = []
                    
                    # Проверка текущей позиции
                    await check_position_health()
                    
                    # Интервал проверки
                    await asyncio.sleep(60)
                    
            except Exception as e:
                logger.error(f"Ошибка в основном цикле: {str(e)}", exc_info=True)
                await asyncio.sleep(120)
                
    except KeyboardInterrupt:
        await telegram_send_message("🛑 Система сигналов остановлена вручную")
    finally:
        await application.stop()

if __name__ == "__main__":
    asyncio.run(signal_monitoring())
