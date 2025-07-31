# Торговая система сигналов для акций ВТБ

[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-blue)](https://core.telegram.org/bots)

Торговая система для скальпинга акций ВТБ (VTB) на Московской бирже с использованием Tinkoff Invest API и Telegram-бота.

## Основные функции
- 📊 Автоматический анализ ценовых данных в реальном времени
- 🚀 Сигналы на покупку/продажу на основе стратегии скользящих средних (MA)
- 📈 Генерация графиков с индикаторами
- 🔔 Уведомления в Telegram с подтверждением действий
- ⚠️ Система управления рисками (стоп-лосс, тейк-профит)
- ⏱️ Мониторинг позиций в реальном времени

## Требования
- Python 3.8+
- Tinkoff Invest API токен
- Telegram Bot API токен
- Учетная запись Tinkoff Invest

## Установка
1. Клонируйте репозиторий:
```bash
git clone https://github.com/yourusername/vtb-trading-bot.git
cd vtb-trading-bot
