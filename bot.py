import logging
import json
import sys
import asyncio
import os
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, CallbackContext
from telegram.error import BadRequest, Forbidden

print("Python version:", sys.version)

# --- КОНФИГУРАЦИЯ ---
DATA_FILE = "data.json"

# НАСТРОЙКИ ЭКОНОМИКИ
COIN_REWARD = 1      # Сколько монет получает пользователь за полученный аккаунт
EXCHANGE_PRICE = 10  # Сколько монет стоит обмен на аккаунт

# ID ГЛАВНЫХ администраторов (Супер-админы, имеют все права и их нельзя удалить)
SUPER_ADMIN_IDS = [7635015201] 
TOKEN = "7862779341:AAFKl6t4RYzdLQ_yVDVaXtUMEXkxf9QZZ_E"

# ПРАВА ДОСТУПА
PERM_BAN = 'ban_users'
PERM_BROADCAST = 'broadcast'
PERM_ACCS = 'manage_accs'
PERM_PROMOS = 'manage_promos'
PERM_CHANNELS = 'manage_channels'
PERM_ADD_ADMIN = 'add_admin'

DEFAULT_PERMISSIONS = {
    PERM_BAN: True,
    PERM_BROADCAST: True,
    PERM_ACCS: True,
    PERM_PROMOS: True,
    PERM_CHANNELS: False, # По умолчанию новые админы не могут менять каналы
    PERM_ADD_ADMIN: False # По умолчанию новые админы не могут добавлять других
}

# --------------------

# Флаг остановки бота
BOT_STOPPED = False

# Структура данных по умолчанию
default_data = {
    "accounts": [], 
    "users": {}, 
    "channels": ["@freeaccountanksblitz", "@buffonshopp"], # Список обязательных каналов
    "admins": {}, # Динамические админы: {"ID": {"permissions": {...}, "added_by": ID}}
    "promocodes": {}, 
    "reviews": [],
    "banned_users": []
}

# Загрузка данных
try:
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
        # Миграция данных (добавление новых полей, если их нет)
        for key, value in default_data.items():
            if key not in data:
                data[key] = value
        # Миграция каналов (если старый формат)
        if "channel" in data:
            if not data.get("channels"):
                data["channels"] = data["channel"]
            del data["channel"]
except FileNotFoundError:
    data = default_data
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
except Exception as e:
    print(f"Ошибка чтения данных: {e}")
    data = default_data

def save():
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- СИСТЕМА ПРАВ ---

def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом (супер или обычным)"""
    if user_id in SUPER_ADMIN_IDS:
        return True
    return str(user_id) in data.get("admins", {})

def check_perm(user_id: int, perm: str) -> bool:
    """Проверяет наличие конкретного права у админа"""
    if user_id in SUPER_ADMIN_IDS:
        return True
    
    admin_data = data.get("admins", {}).get(str(user_id))
    if not admin_data:
        return False
    
    return admin_data.get("permissions", {}).get(perm, False)

# --- КЛАВИАТУРЫ ПОЛЬЗОВАТЕЛЯ (REPLY) ---
def menu(user_id: int):
    kb = [
        ["🎮 Получить аккаунт", "📜 История"],
        ["💎 Обменять монеты", "🎟 Промокод"],
        ["📢 Канал", "💬 Поддержка"],
        ["⭐ Отзывы", "ℹ️ FAQ"],
        ["✅ Проверить подписку", "👤 Мой профиль"]
    ]
    if is_admin(user_id):
        kb.append(["👑 Админ"])

    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def reviews_keyboard():
    keyboard = [
        [InlineKeyboardButton("📝 Посмотреть отзывы", callback_data="view_reviews")],
        [InlineKeyboardButton("⭐ Оставить отзыв", callback_data="leave_review")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_sub_keyboard(channels_list):
    """Клавиатура для подписки на каналы"""
    kb = []
    for ch in channels_list:
        label = ch
        url = ch
        
        # Формируем ссылку
        if ch.startswith("@"):
            url = f"https://t.me/{ch[1:]}"
        elif "t.me" not in ch:
            # Если это просто ID или что-то без ссылки, пытаемся сделать ссылку
            # Но лучше, если админ вводит @username или https://t.me/...
            url = f"https://t.me/{ch}"
        
        kb.append([InlineKeyboardButton(f"Подписаться", url=url)])
    
    # Кнопка проверки
    kb.append([InlineKeyboardButton("✅ Я подписался", callback_data="check_sub_confirm")])
    return InlineKeyboardMarkup(kb)


# --- КЛАВИАТУРЫ АДМИНА (INLINE) ---

def admin_kb_main(user_id):
    """Главное меню админки с учетом прав"""
    status_icon = "▶️" if not BOT_STOPPED else "⏸"
    kb = []
    
    # Статистика доступна всем админам
    kb.append([InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")])
    
    row2 = []
    if check_perm(user_id, PERM_ACCS):
        row2.append(InlineKeyboardButton("📦 Аккаунты", callback_data="admin_menu_accs"))
    if check_perm(user_id, PERM_PROMOS):
        row2.append(InlineKeyboardButton("🎟 Промокоды", callback_data="admin_menu_promo"))
    if row2: kb.append(row2)

    row3 = [InlineKeyboardButton("⭐ Отзывы", callback_data="admin_menu_reviews")]
    if check_perm(user_id, PERM_BAN):
        row3.append(InlineKeyboardButton("👥 Пользователи", callback_data="admin_menu_users"))
    kb.append(row3)

    row4 = []
    if check_perm(user_id, PERM_BROADCAST):
        row4.append(InlineKeyboardButton("📣 Рассылка", callback_data="admin_broadcast"))
    row4.append(InlineKeyboardButton("✉️ ЛС", callback_data="admin_pm"))
    kb.append(row4)

    row5 = []
    if check_perm(user_id, PERM_CHANNELS):
        row5.append(InlineKeyboardButton("📢 Каналы", callback_data="admin_menu_channels"))
    if check_perm(user_id, PERM_ADD_ADMIN):
        row5.append(InlineKeyboardButton("🛡 Админы", callback_data="admin_menu_admins"))
    if row5: kb.append(row5)

    kb.append([InlineKeyboardButton(f"{status_icon} Стоп/Старт Бота", callback_data="admin_toggle_bot")])
    kb.append([InlineKeyboardButton("❌ Закрыть панель", callback_data="admin_close")])
    
    return InlineKeyboardMarkup(kb)

def admin_kb_accounts():
    kb = [
        [InlineKeyboardButton("🔄 Загрузить аккаунты (TXT)", callback_data="admin_acc_load")],
        [InlineKeyboardButton("❌ Удалить ВСЕ аккаунты", callback_data="admin_acc_del_all")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_main")]
    ]
    return InlineKeyboardMarkup(kb)

def admin_kb_channels():
    kb = [
        [InlineKeyboardButton("➕ Добавить канал", callback_data="admin_channel_add")],
        [InlineKeyboardButton("➖ Удалить канал", callback_data="admin_channel_del")],
        [InlineKeyboardButton("📋 Список каналов", callback_data="admin_channel_list")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_main")]
    ]
    return InlineKeyboardMarkup(kb)

def admin_kb_admins_list():
    """Генерация списка админов для редактирования"""
    kb = []
    # Добавляем кнопки для существующих админов
    for adm_id in data.get("admins", {}):
        kb.append([InlineKeyboardButton(f"👤 {adm_id}", callback_data=f"adm_edit:{adm_id}")])
    
    kb.append([InlineKeyboardButton("➕ Назначить админа", callback_data="admin_add_new")])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_main")])
    return InlineKeyboardMarkup(kb)

def admin_kb_admin_rights(target_id):
    """Права конкретного админа"""
    perms = data.get("admins", {}).get(str(target_id), {}).get("permissions", {})
    
    def p_btn(key, text):
        status = "✅" if perms.get(key, False) else "❌"
        return InlineKeyboardButton(f"{status} {text}", callback_data=f"adm_toggle:{target_id}:{key}")

    kb = [
        [p_btn(PERM_ACCS, "Аккаунты"), p_btn(PERM_PROMOS, "Промо")],
        [p_btn(PERM_BAN, "Бан"), p_btn(PERM_BROADCAST, "Рассылка")],
        [p_btn(PERM_CHANNELS, "Каналы"), p_btn(PERM_ADD_ADMIN, "Админы")],
        [InlineKeyboardButton("🗑 УДАЛИТЬ АДМИНА", callback_data=f"adm_delete:{target_id}")],
        [InlineKeyboardButton("🔙 К списку", callback_data="admin_menu_admins")]
    ]
    return InlineKeyboardMarkup(kb)

def admin_kb_promo():
    kb = [
        [InlineKeyboardButton("🎟 Создать промокод", callback_data="admin_promo_create")],
        [InlineKeyboardButton("📋 Список активных", callback_data="admin_promo_list")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_main")]
    ]
    return InlineKeyboardMarkup(kb)

def admin_kb_reviews():
    kb = [
        [InlineKeyboardButton("📝 Читать все", callback_data="admin_review_all")],
        [InlineKeyboardButton("🗑 Очистить ВСЕ", callback_data="admin_review_clear_all")],
        [InlineKeyboardButton("❌ Удалить по номеру", callback_data="admin_review_del_one")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_main")]
    ]
    return InlineKeyboardMarkup(kb)

def admin_kb_users():
    kb = [
        [InlineKeyboardButton("⛔ Забанить ID", callback_data="admin_user_ban")],
        [InlineKeyboardButton("✅ Разбанить ID", callback_data="admin_user_unban")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_main")]
    ]
    return InlineKeyboardMarkup(kb)

def back_btn(callback_data="admin_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=callback_data)]])


# --- ЛОГИКА БОТА ---

# Старт
async def start(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned_users", []):
        await update.message.reply_text("❌ Вы заблокированы.")
        return

    if user_id not in data["users"]:
        data["users"][user_id] = {
            "name": user.full_name,
            "username": user.username,
            "coins": 0,
            "received": 0,
            "used_promocodes": [],
            "history": [],
            "join_date": datetime.now().isoformat()
        }
        save()


    text = f"""🎮 <b>Добро пожаловать!</b>

🤖 Я бот по бесплатной раздаче аккаунтов!
💬 Поддержка: @texpoddergka2026_bot

🔹 <b>Лимит:</b> 1 аккаунт в 24 часа.
🔹 <b>Монеты:</b> За каждый аккаунт получаете {COIN_REWARD} монету
🔹 <b>Обмен:</b> {EXCHANGE_PRICE} монет = 1 аккаунт
🔹 <b>Формат:</b> почта:пароль

Выберите действие из меню ниже:"""

    await update.message.reply_text(text, parse_mode='HTML', reply_markup=menu(user.id))


# Команда /panel для админ панели
async def panel_command(update: Update, context: CallbackContext):
    user = update.effective_user
    
    if is_admin(user.id):
        await update.message.reply_text("👑 <b>Админ панель v2.0</b>\nВыберите раздел:", parse_mode='HTML', reply_markup=admin_kb_main(user.id))
    else:
        await update.message.reply_text("❌ У вас нет доступа.", reply_markup=menu(user.id))


# Поддержка
async def support(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот выключен")
        return

    await update.message.reply_text("💬 Поддержка: @texpoddergka2026_bot", reply_markup=menu(update.effective_user.id))


# Получить аккаунт
async def get_account(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned_users", []):
        await update.message.reply_text("❌ Вы заблокированы.")
        return

    # ПРОВЕРКА ПОДПИСКИ
    is_sub, not_sub_list = await check_subscription_logic(user.id, context)
    if not is_sub:
        await update.message.reply_text(
            f"🛑 <b>Доступ ограничен!</b>\n\n"
            f"Для получения аккаунтов необходимо подписаться на наших спонсоров:",
            parse_mode='HTML',
            reply_markup=get_sub_keyboard(not_sub_list)
        )
        return

    if not data["accounts"]:
        await update.message.reply_text("❌ Нет аккаунтов", reply_markup=menu(user.id))
        return

    user_data = data["users"][user_id]

    if user_data.get("last_receive"):
        last_time = datetime.fromisoformat(user_data["last_receive"])
        if datetime.now() - last_time < timedelta(hours=24):
            next_time = last_time + timedelta(hours=24)
            wait = next_time - datetime.now()
            hours = wait.seconds // 3600
            minutes = (wait.seconds % 3600) // 60
            await update.message.reply_text(
                f"⏰ <b>Лимит: 1 аккаунт в 24 часа</b>\n\n"
                f"Следующий аккаунт можно получить через:\n"
                f"<b>{hours} часов {minutes} минут</b>",
                parse_mode='HTML',
                reply_markup=menu(user.id)
            )
            return

    account = data["accounts"].pop(0)
    user_data["coins"] += COIN_REWARD
    user_data["received"] += 1
    user_data["last_receive"] = datetime.now().isoformat()
    user_data["history"] = user_data.get("history", []) + [{
        "date": datetime.now().isoformat(),
        "account": account
    }]

    save()

    await update.message.reply_text(
        f"✅ <b>Аккаунт получен!</b>\n\n"
        f"🔐 <code>{account}</code>\n\n"
        f"💎 +{COIN_REWARD} монета\n"
        f"💰 Всего: {user_data['coins']} монет\n\n"
        f"⚠️ <b>Следующий через 24 часа</b>",
        parse_mode='HTML',
        reply_markup=menu(user.id)
    )


# Профиль
async def profile(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned_users", []):
        await update.message.reply_text("❌ Вы заблокированы.")
        return

    if user_id in data["users"]:
        user_data = data["users"][user_id]
        used_promo = len(user_data.get("used_promocodes", []))

        time_text = ""
        if user_data.get("last_receive"):
            last = datetime.fromisoformat(user_data["last_receive"])
            next_time = last + timedelta(hours=24)
            if datetime.now() < next_time:
                wait = next_time - datetime.now()
                hours = wait.seconds // 3600
                minutes = (wait.seconds % 3600) // 60
                time_text = f"\n⏰ Следующий через: {hours}ч {minutes}м"
            else:
                time_text = "\n✅ Можете получить аккаунт"

        text = f"""👤 <b>Профиль</b>

🆔 ID: {user_id}
👤 Имя: {user_data['name']}
📅 Зарегистрирован: {datetime.fromisoformat(user_data['join_date']).strftime('%d.%m.%Y')}
🎮 Получено аккаунтов: {user_data['received']}
💎 Монеты: {user_data['coins']}
🎟 Использовано промокодов: {used_promo}{time_text}

💎 <b>Обмен монет:</b>
1 аккаунт = {EXCHANGE_PRICE} монет
Можно обменять: {user_data['coins'] // EXCHANGE_PRICE} аккаунт(ов)"""

        await update.message.reply_text(text, parse_mode='HTML', reply_markup=menu(user.id))
    else:
        await update.message.reply_text("❌ Профиль не найден", reply_markup=menu(user.id))


# История
async def account_history(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned_users", []):
        await update.message.reply_text("❌ Вы заблокированы.")
        return

    if user_id not in data["users"]:
        await update.message.reply_text("❌ Сначала запустите бота /start", reply_markup=menu(user.id))
        return

    user_data = data["users"][user_id]
    history = user_data.get("history", [])

    if not history:
        await update.message.reply_text("📜 Вы еще не получали аккаунты", reply_markup=menu(user.id))
        return

    text = "📜 <b>История получения аккаунтов:</b>\n\n"

    for i, item in enumerate(history[-10:], 1):
        date = datetime.fromisoformat(item["date"]).strftime("%d.%m.%Y %H:%M")
        account = item["account"]
        text += f"{i}. {date}\n   <code>{account}</code>\n\n"

    if len(history) > 10:
        text += f"\n📊 Всего получено: {len(history)} аккаунтов"

    await update.message.reply_text(text, parse_mode='HTML', reply_markup=menu(user.id))


# Обмен монет
async def exchange_coins(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned_users", []):
        await update.message.reply_text("❌ Вы заблокированы.")
        return

    if user_id not in data["users"]:
        await update.message.reply_text("❌ Сначала запустите бота /start", reply_markup=menu(user.id))
        return

    user_data = data["users"][user_id]
    coins = user_data["coins"]

    if coins < EXCHANGE_PRICE:
        await update.message.reply_text(
            f"❌ Недостаточно монет!\n\n"
            f"Ваши монеты: {coins}\n"
            f"Нужно для обмена: {EXCHANGE_PRICE}\n\n"
            f"1 аккаунт = {EXCHANGE_PRICE} монет",
            reply_markup=menu(user.id)
        )
        return

    if not data["accounts"]:
        await update.message.reply_text("❌ Нет аккаунтов для выдачи!", reply_markup=menu(user.id))
        return

    account = data["accounts"].pop(0)
    user_data["coins"] = coins - EXCHANGE_PRICE
    user_data["history"] = user_data.get("history", []) + [{
        "date": datetime.now().isoformat(),
        "account": account,
        "type": "exchange"
    }]

    save()

    await update.message.reply_text(
        f"✅ <b>Обмен выполнен!</b>\n\n"
        f"🎮 Получен аккаунт\n"
        f"💎 Списано монет: {EXCHANGE_PRICE}\n"
        f"💰 Осталось монет: {user_data['coins']}\n\n"
        f"🔐 Аккаунт:\n<code>{account}</code>",
        parse_mode='HTML',
        reply_markup=menu(user.id)
    )


# FAQ
async def faq(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    text = f"""ℹ️ <b>FAQ - Часто задаваемые вопросы</b>

🔹 <b>Лимит:</b> 1 аккаунт в 24 часа.
🔹 <b>Бонусы:</b> Приглашай друзей и получай аккаунты без очереди.
🔹 <b>Формат аккаунтов:</b> почта:пароль
🔹 <b>Система монет:</b> За каждый полученный аккаунт вы получаете {COIN_REWARD} монету
🔹 <b>Обмен:</b> 1 аккаунт = {EXCHANGE_PRICE} монет
🔹 <b>Промокоды:</b> Дают аккаунты, использовать можно только 1 раз
🔹 <b>Поддержка:</b> @texpoddergka2026_bot

📢 <b>Важно:</b> Сохраняйте полученные аккаунты!"""

    await update.message.reply_text(text, parse_mode='HTML', reply_markup=menu(user.id))


# ОБЩИЙ ОБРАБОТЧИК CALLBACK КНОПОК
async def main_callback_handler(update: Update, context: CallbackContext):
    global BOT_STOPPED
    
    query = update.callback_query
    cb_data = query.data 
    user_id = query.from_user.id
    
    # --- ОБРАБОТКА ПОЛЬЗОВАТЕЛЬСКИХ КНОПОК ---
    if cb_data == "view_reviews":
        await view_reviews(update, context)
        await query.answer()
        return
    elif cb_data == "leave_review":
        await leave_review_handler(update, context)
        await query.answer()
        return
    elif cb_data == "check_sub_confirm":
        # Проверка подписки по кнопке "Я подписался"
        await query.answer()
        is_sub, not_sub_list = await check_subscription_logic(user_id, context)
        if is_sub:
             await query.edit_message_text(
                "✅ <b>Отлично! Вы подписаны.</b>\nТеперь можете пользоваться ботом.",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                f"❌ <b>Вы все еще не подписаны!</b>\n\nПодпишитесь на каналы ниже:",
                parse_mode='HTML',
                reply_markup=get_sub_keyboard(not_sub_list)
            )
        return

    # --- ОБРАБОТКА АДМИНСКИХ КНОПОК ---
    if not is_admin(user_id):
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    await query.answer()

    try:
        # НАВИГАЦИЯ ПО МЕНЮ
        if cb_data == "admin_main":
            await query.edit_message_text("👑 <b>Админ панель v2.0</b>\nВыберите раздел:", parse_mode='HTML', reply_markup=admin_kb_main(user_id))
        
        elif cb_data == "admin_menu_accs":
            if not check_perm(user_id, PERM_ACCS):
                await query.answer("⛔ Нет прав на управление аккаунтами", show_alert=True)
                return
            await query.edit_message_text(f"📦 <b>Управление аккаунтами</b>\nВсего в наличии: {len(data['accounts'])}", parse_mode='HTML', reply_markup=admin_kb_accounts())
            
        elif cb_data == "admin_menu_promo":
            if not check_perm(user_id, PERM_PROMOS):
                await query.answer("⛔ Нет прав на управление промо", show_alert=True)
                return
            await query.edit_message_text("🎟 <b>Управление промокодами</b>", parse_mode='HTML', reply_markup=admin_kb_promo())
            
        elif cb_data == "admin_menu_reviews":
            await query.edit_message_text(f"⭐ <b>Управление отзывами</b>\nВсего отзывов: {len(data.get('reviews', []))}", parse_mode='HTML', reply_markup=admin_kb_reviews())
            
        elif cb_data == "admin_menu_users":
            if not check_perm(user_id, PERM_BAN):
                await query.answer("⛔ Нет прав на бан пользователей", show_alert=True)
                return
            await query.edit_message_text(f"👥 <b>Управление пользователями</b>\nВсего юзеров: {len(data['users'])}\nВ бане: {len(data.get('banned_users', []))}", parse_mode='HTML', reply_markup=admin_kb_users())

        elif cb_data == "admin_menu_channels":
            if not check_perm(user_id, PERM_CHANNELS):
                await query.answer("⛔ Нет прав на управление каналами", show_alert=True)
                return
            await query.edit_message_text(f"📢 <b>Управление каналами</b>\nПользователи обязаны подписаться на них.", parse_mode='HTML', reply_markup=admin_kb_channels())
        
        elif cb_data == "admin_menu_admins":
            if not check_perm(user_id, PERM_ADD_ADMIN):
                await query.answer("⛔ Нет прав на управление админами", show_alert=True)
                return
            await query.edit_message_text("🛡 <b>Управление администраторами</b>\nВыберите админа для настройки прав:", parse_mode='HTML', reply_markup=admin_kb_admins_list())

        elif cb_data == "admin_close":
            await query.delete_message()
        
        # --- ФУНКЦИОНАЛ АДМИНКИ ---
        
        # Статистика
        elif cb_data == "admin_stats":
            total_accounts = sum(user.get("received", 0) for user in data["users"].values())
            total_coins = sum(user.get("coins", 0) for user in data["users"].values())
            banned_count = len(data.get("banned_users", []))
            stats = f"""📊 <b>Статистика бота</b>

👥 Пользователей: {len(data["users"])}
⛔ Забанено: {banned_count}
📦 Аккаунтов в наличии: {len(data["accounts"])}
🎮 Всего выдано аккаунтов: {total_accounts}
💰 Всего монет у пользователей: {total_coins}
🎟 Промокодов: {len(data["promocodes"])}
⭐ Отзывов: {len(data.get("reviews", []))}
📢 Каналов: {len(data.get("channels", []))}
🛡 Админов (доп): {len(data.get("admins", {}))}
⏸ Бот {'остановлен' if BOT_STOPPED else 'работает'}"""
            await query.edit_message_text(stats, parse_mode='HTML', reply_markup=back_btn())

        # Аккаунты
        elif cb_data == "admin_acc_load":
            await query.message.reply_text(
                "🔄 <b>Загрузка аккаунтов из файла</b>\n\n"
                "Отправьте мне файл .txt с аккаунтами в формате:\n"
                "<code>почта:пароль</code>\n\n"
                "Каждый аккаунт с новой строки.",
                parse_mode='HTML'
            )
            context.user_data["uploading_accounts"] = True

        elif cb_data == "admin_acc_del_all":
            count = len(data["accounts"])
            data["accounts"] = []
            save()
            await query.edit_message_text(f"✅ Удалено {count} аккаунтов!\nТеперь в наличии: 0", reply_markup=admin_kb_accounts())

        # Каналы
        elif cb_data == "admin_channel_list":
            channels = data.get("channels", [])
            if not channels:
                text = "📭 Список каналов пуст."
            else:
                text = "📢 <b>Список обязательных каналов:</b>\n\n" + "\n".join(channels)
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=admin_kb_channels())
            
        elif cb_data == "admin_channel_add":
            await query.message.reply_text("➕ <b>Добавление канала</b>\nВведите @username канала или ID (бот должен быть админом в канале!):", parse_mode='HTML')
            context.user_data["adding_channel"] = True

        elif cb_data == "admin_channel_del":
            await query.message.reply_text("➖ <b>Удаление канала</b>\nВведите @username канала для удаления:", parse_mode='HTML')
            context.user_data["deleting_channel"] = True

        # Админы
        elif cb_data == "admin_add_new":
            await query.message.reply_text("👤 <b>Добавление админа</b>\nВведите числовой ID пользователя:", parse_mode='HTML')
            context.user_data["adding_admin"] = True

        elif cb_data.startswith("adm_edit:"):
            target_id = cb_data.split(":")[1]
            await query.edit_message_text(f"⚙️ <b>Настройка прав для {target_id}</b>", parse_mode='HTML', reply_markup=admin_kb_admin_rights(target_id))

        elif cb_data.startswith("adm_toggle:"):
            parts = cb_data.split(":")
            target_id, perm_key = parts[1], parts[2]
            
            # Меняем право
            if str(target_id) in data.get("admins", {}):
                current = data["admins"][str(target_id)]["permissions"].get(perm_key, False)
                data["admins"][str(target_id)]["permissions"][perm_key] = not current
                save()
                await query.edit_message_reply_markup(reply_markup=admin_kb_admin_rights(target_id))
            else:
                await query.answer("Админ не найден", show_alert=True)
                await query.edit_message_text("🛡 Админы", reply_markup=admin_kb_admins_list())

        elif cb_data.startswith("adm_delete:"):
            target_id = cb_data.split(":")[1]
            if str(target_id) in data.get("admins", {}):
                del data["admins"][str(target_id)]
                save()
                await query.answer("Админ удален")
                await query.edit_message_text("🛡 Админы", reply_markup=admin_kb_admins_list())
            else:
                await query.answer("Ошибка удаления", show_alert=True)

        # Промокоды
        elif cb_data == "admin_promo_create":
            await query.message.reply_text(
                "🎟 <b>Создание промокода</b>\n\n"
                "Введите данные в формате:\n"
                "<code>КОД КОЛИЧЕСТВО_АККАУНТОВ КОЛИЧЕСТВО_ИСПОЛЬЗОВАНИЙ</code>\n\n"
                "Пример: SUMMER10 2 50",
                parse_mode='HTML'
            )
            context.user_data["creating_promo"] = True

        elif cb_data == "admin_promo_list":
            promocodes = data.get("promocodes", {})
            if not promocodes:
                await query.edit_message_text("❌ Нет активных промокодов", reply_markup=admin_kb_promo())
                return
            text = "📋 <b>Активные промокоды:</b>\n\n"
            for code, promo in promocodes.items():
                remaining = promo.get("max_uses", 1) - promo.get("used", 0)
                text += f"🎟 <b>{code}</b>\n   • Аккаунтов: {promo.get('reward', 1)}\n   • Осталось: {remaining}\n\n"
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=admin_kb_promo())

        # Отзывы
        elif cb_data == "admin_review_all":
            reviews = data.get("reviews", [])
            if not reviews:
                await query.edit_message_text("❌ Нет отзывов", reply_markup=admin_kb_reviews())
                return
            text = "⭐ <b>Все отзывы:</b>\n\n"
            for i, review in enumerate(reviews, 1):
                date = datetime.fromisoformat(review["date"]).strftime("%d.%m.%Y")
                text += f"<b>#{i}</b> {review['user_name']} ({date}):\n{review['text'][:50]}...\n\n"
            if len(text) > 4000: text = text[:4000] + "..."
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=admin_kb_reviews())

        elif cb_data == "admin_review_clear_all":
            data["reviews"] = []
            save()
            await query.edit_message_text("✅ Все отзывы удалены!", reply_markup=admin_kb_reviews())

        elif cb_data == "admin_review_del_one":
            await query.message.reply_text(
                "🗑 <b>УДАЛЕНИЕ ОТЗЫВА</b>\n\n"
                "Используйте команду: <code>/delete_review НОМЕР</code>\n"
                "Чтобы узнать номер, нажмите '📝 Читать все'",
                parse_mode='HTML'
            )

        # Юзеры
        elif cb_data == "admin_user_ban":
            await query.message.reply_text("⛔ <b>Блокировка</b>\nВведите ID пользователя:", parse_mode='HTML')
            context.user_data["banning_user"] = True

        elif cb_data == "admin_user_unban":
            await query.message.reply_text("✅ <b>Разблокировка</b>\nВведите ID пользователя:", parse_mode='HTML')
            context.user_data["unbanning_user"] = True

        # Рассылка и ЛС
        elif cb_data == "admin_broadcast":
            if not check_perm(user_id, PERM_BROADCAST):
                await query.answer("⛔ Нет прав на рассылку", show_alert=True)
                return
            await query.message.reply_text("📣 <b>РАССЫЛКА</b>\nВведите сообщение для рассылки (поддерживается HTML):", parse_mode='HTML')
            context.user_data["broadcasting"] = True

        elif cb_data == "admin_pm":
            await query.message.reply_text("✉️ <b>ЛС</b>\nВведите: <code>ID СООБЩЕНИЕ</code>", parse_mode='HTML')
            context.user_data["sending_private"] = True

        # Управление ботом
        elif cb_data == "admin_toggle_bot":
            BOT_STOPPED = not BOT_STOPPED
            status = "ОСТАНОВЛЕН 🔴" if BOT_STOPPED else "ЗАПУЩЕН 🟢"
            # Для ответа на callback (всплывающее уведомление)
            await query.answer(f"Бот {status}")
            await query.edit_message_reply_markup(reply_markup=admin_kb_main(user_id))

    except BadRequest as e:
        # Игнорируем ошибку "Message is not modified" (сообщение не изменено)
        if "Message is not modified" not in str(e):
            print(f"Ошибка при обработке callback: {e}")


# Отзывы меню (для пользователя)
async def reviews_menu(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    await update.message.reply_text(
        "⭐ <b>Отзывы</b>\n\n"
        "Выберите действие:",
        parse_mode='HTML',
        reply_markup=reviews_keyboard()
    )


# Оставить отзыв
async def leave_review_handler(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned_users", []):
        await update.message.reply_text("❌ Вы заблокированы.")
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="⭐ <b>Оставить отзыв</b>\n\n"
             "Напишите ваш отзыв о боте:\n\n"
             "Можно оценить от 1 до 5 звезд и написать комментарий\n\n"
             "Пример: ⭐⭐⭐⭐⭐ Отличный бот, все работает!",
        parse_mode='HTML'
    )
    context.user_data["leaving_review"] = True


# Посмотреть отзывы (для пользователя)
async def view_reviews(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    reviews = data.get("reviews", [])

    if not reviews:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📝 Пока нет отзывов. Будьте первым!",
            reply_markup=reviews_keyboard()
        )
        return

    text = "⭐ <b>Отзывы о боте:</b>\n\n"

    for i, review in enumerate(reviews[-10:], 1):
        date = datetime.fromisoformat(review["date"]).strftime("%d.%m.%Y")
        text += f"{i}. {review['text']}\n   👤 {review['user_name']} • {date}\n\n"

    if len(reviews) > 10:
        text += f"\n📊 Всего отзывов: {len(reviews)}"

    # Редактируем сообщение, если вызвано через callback, или отправляем новое
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=text, parse_mode='HTML', reply_markup=reviews_keyboard())
        except BadRequest:
            pass # Игнорируем, если текст тот же
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode='HTML',
            reply_markup=reviews_keyboard()
        )


# ЛОГИКА ПРОВЕРКИ ПОДПИСКИ
async def check_subscription_logic(user_id: int, context: CallbackContext):
    """
    Возвращает кортеж (bool, list), где bool - подписан ли на все,
    list - список названий каналов, на которые не подписан.
    СТРОГАЯ ПРОВЕРКА: Если бот не может проверить (ошибка), считает что НЕ подписан.
    """
    channels = data.get("channels", [])
    if not channels:
        return True, []
    
    not_subscribed = []
    
    for channel in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            
            # Check for 'left' or 'kicked'
            if member.status in ['left', 'kicked']:
                not_subscribed.append(channel)
            # Check for 'restricted' but not a member (very rare, usually restricted is member)
            elif member.status == 'restricted' and not getattr(member, 'is_member', True):
                not_subscribed.append(channel)
                
        except BadRequest:
            # Бот не админ или чат не найден -> Считаем, что не подписан (Строгий режим)
            # Это заставляет админа исправить конфиг, а юзера - не пропускает через баг
            not_subscribed.append(channel)
        except Exception as e:
            print(f"Error checking {channel}: {e}")
            not_subscribed.append(channel)

    if not_subscribed:
        return False, not_subscribed
    return True, []

# Проверить подписку (команда меню)
async def check_subscription(update: Update, context: CallbackContext):
    user = update.effective_user
    is_sub, not_sub_list = await check_subscription_logic(user.id, context)
    
    if is_sub:
        await update.message.reply_text(
            "✅ Вы подписаны на все каналы!",
            reply_markup=menu(user.id)
        )
    else:
        await update.message.reply_text(
            f"❌ Вы не подписаны на каналы. Подпишитесь, чтобы продолжить:",
            reply_markup=get_sub_keyboard(not_sub_list)
        )

# Активация промокода
async def activate_promocode(update: Update, context: CallbackContext):
    global BOT_STOPPED
    if BOT_STOPPED and not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Бот временно остановлен.")
        return

    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned_users", []):
        await update.message.reply_text("❌ Вы заблокированы.")
        return
    
    # ПРОВЕРКА ПОДПИСКИ ДЛЯ ПРОМОКОДА
    is_sub, not_sub_list = await check_subscription_logic(user.id, context)
    if not is_sub:
        await update.message.reply_text(
            f"🛑 Подпишитесь на каналы для активации промокода:",
            reply_markup=get_sub_keyboard(not_sub_list)
        )
        return

    if user_id not in data["users"]:
        await update.message.reply_text("❌ Сначала запустите бота /start", reply_markup=menu(user.id))
        return

    await update.message.reply_text( "🎟 <b>Активация промокода</b>\n\n" "Введите промокод:", parse_mode='HTML'
)
    context.user_data["waiting_promo"] = True


# Обработка промокода
async def process_promocode(update: Update, context: CallbackContext):
    if context.user_data.get("waiting_promo"):
        text = update.message.text.strip().upper()
        context.user_data["waiting_promo"] = False

        user = update.effective_user
        user_id = str(user.id)
        user_data = data["users"][user_id]

        if text in user_data.get("used_promocodes", []):
            await update.message.reply_text("❌ Вы уже использовали этот промокод!",
                                            reply_markup=menu(user.id))
            return

        if text not in data["promocodes"]:
            await update.message.reply_text("❌ Промокод не найден!", reply_markup=menu(user.id))
            return

        promo = data["promocodes"][text]

        if promo.get("used", 0) >= promo.get("max_uses", 1):
            await update.message.reply_text("❌ Промокод уже использован максимальное количество раз!",
                                            reply_markup=menu(user.id))
            return

        if not data["accounts"]:
            await update.message.reply_text("❌ Нет аккаунтов для выдачи!", reply_markup=menu(user.id))
            return

        accounts_to_give = min(promo.get("reward", 1), len(data["accounts"]))
        accounts = []

        for _ in range(accounts_to_give):
            accounts.append(data["accounts"].pop(0))

        promo["used"] = promo.get("used", 0) + 1
        user_data["used_promocodes"] = user_data.get("used_promocodes", []) + [text]

        for account in accounts:
            user_data["history"] = user_data.get("history", []) + [{
                "date": datetime.now().isoformat(),
                "account": account,
                "type": "promocode"
            }]

        save()

        accounts_text = "\n".join([f"{i + 1}. <code>{acc}</code>" for i, acc in enumerate(accounts)])

        await update.message.reply_text(
            f"✅ <b>Промокод активирован!</b>\n\n"
            f"🎁 Получено аккаунтов: {accounts_to_give}\n\n"
            f"🔐 Аккаунты:\n{accounts_text}\n\n"
            f"⚠️ Этот промокод больше нельзя использовать!",
            parse_mode='HTML',
            reply_markup=menu(user.id)
        )


# Команда для удаления отзыва
async def delete_review(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Только для администраторов")
        return

    if not context.args:
        await update.message.reply_text(
            "ℹ️ <b>Использование команды:</b>\n"
            "<code>/delete_review НОМЕР_ОТЗЫВА</code>\n\n"
            "📌 <b>Пример:</b>\n"
            "<code>/delete_review 1</code>",
            parse_mode='HTML'
        )
        return

    try:
        review_number = int(context.args[0]) - 1  # Нумерация с 1, а индексы с 0
        reviews = data.get("reviews", [])

        if review_number < 0 or review_number >= len(reviews):
            await update.message.reply_text(
                f"❌ <b>НЕВЕРНЫЙ НОМЕР ОТЗЫВА!</b>\n\n"
                f"Всего отзывов: {len(reviews)}\n"
                f"Введите номер от 1 до {len(reviews)}",
                parse_mode='HTML'
            )
            return

        # Получаем удаляемый отзыв
        deleted_review = reviews[review_number]

        # Удаляем отзыв
        del reviews[review_number]
        data["reviews"] = reviews
        save()

        # Формируем информацию об удаленном отзыве
        deleted_text = deleted_review['text'][:100] + ('...' if len(deleted_review['text']) > 100 else '')
        deleted_date = datetime.fromisoformat(deleted_review['date']).strftime("%d.%m.%Y %H:%M")

        await update.message.reply_text(
            f"✅ <b>ОТЗЫВ УДАЛЕН!</b>\n\n"
            f"📋 <b>Информация об удаленном отзыве:</b>\n"
            f"👤 <b>Пользователь:</b> {deleted_review['user_name']}\n"
            f"🆔 <b>ID:</b> <code>{deleted_review['user_id']}</code>\n"
            f"📅 <b>Дата:</b> {deleted_date}\n"
            f"💬 <b>Текст отзыва:</b>\n{deleted_text}\n\n"
            f"📊 Осталось отзывов: {len(data['reviews'])}"
        )
        # Если есть активное меню отзывов, оно обновится при следующем нажатии кнопок

    except ValueError:
        await update.message.reply_text(
            "❌ <b>НЕВЕРНЫЙ ФОРМАТ НОМЕРА!</b>\n\n"
            "Номер отзыва должен быть числом.\n"
            "Пример: <code>/delete_review 5</code>",
            parse_mode='HTML'
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>ОШИБКА ПРИ УДАЛЕНИИ!</b>\n\n"
            f"{str(e)}",
            parse_mode='HTML'
        )


# КОМАНДА /info ДЛЯ АДМИНА
async def user_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Только для администраторов")
        return

    if context.args:
        target_id = context.args[0]
        if target_id in data["users"]:
            user_data = data["users"][target_id]

            # Считаем активность
            history = user_data.get('history', [])
            if history:
                last_date = datetime.fromisoformat(history[-1]["date"])
                last_activity = last_date.strftime("%d.%m.%Y %H:%M")
            else:
                last_activity = "никогда"

            info = f"""👤 <b>ПОЛНАЯ ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ</b>

🆔 <b>ID:</b> <code>{target_id}</code>
👤 <b>Имя:</b> {user_data['name']}
📛 <b>Username:</b> @{user_data.get('username', 'не указан')}
📅 <b>Регистрация:</b> {datetime.fromisoformat(user_data['join_date']).strftime('%d.%m.%Y %H:%M')}
🕐 <b>Последняя активность:</b> {last_activity}

💰 <b>Баланс:</b>
🎮 Получено аккаунтов: {user_data['received']}
💎 Монеты: {user_data['coins']}
🎟 Использовано промокодов: {len(user_data.get('used_promocodes', []))}

📊 <b>Статистика:</b>
📜 История: {len(history)} записей
🔨 Статус: {'⛔ <b>ЗАБАНЕН</b>' if target_id in data.get('banned_users', []) else '✅ <b>АКТИВЕН</b>'}

📝 <b>Последние 3 аккаунта:</b>"""

            if history:
                for i, item in enumerate(history[-3:], 1):
                    date = datetime.fromisoformat(item["date"]).strftime("%d.%m.%Y %H:%M")
                    account = item["account"]
                    info += f"\n{i}. {date}: <code>{account}</code>"
            else:
                info += "\n📭 Аккаунты не получались"

            await update.message.reply_text(info, parse_mode='HTML')
        else:
            await update.message.reply_text(f"❌ Пользователь с ID {target_id} не найден")
    else:
        await update.message.reply_text(
            "ℹ️ <b>Использование команды:</b>\n"
            "<code>/info ID_ПОЛЬЗОВАТЕЛЯ</code>",
            parse_mode='HTML'
        )


# ОБРАБОТКА РАССЫЛКИ
async def process_broadcast(update: Update, context: CallbackContext):
    if context.user_data.get("broadcasting"):
        message_text = update.message.text
        context.user_data["broadcasting"] = False

        user_count = 0
        success_count = 0
        failed_count = 0
        failed_users = []

        # Сообщаем о начале рассылки
        status_msg = await update.message.reply_text(
            f"📤 <b>НАЧИНАЮ РАССЫЛКУ...</b>\n"
            f"👥 Получателей: {len(data['users'])}\n"
            f"📝 Сообщение: {message_text[:50]}...\n\n"
            f"⏳ <i>Это может занять несколько минут...</i>",
            parse_mode='HTML'
        )

        # Рассылаем всем пользователям
        for user_id in list(data["users"].keys()):
            user_count += 1

            try:
                # Пропускаем забаненных
                if user_id in data.get("banned_users", []):
                    continue

                # Обновляем статус каждые 10 пользователей
                if user_count % 10 == 0:
                    await status_msg.edit_text(
                        f"📤 <b>РАССЫЛКА В ПРОЦЕССЕ...</b>\n"
                        f"✅ Отправлено: {success_count}\n"
                        f"❌ Ошибок: {failed_count}\n"
                        f"👥 Обработано: {user_count}/{len(data['users'])}",
                        parse_mode='HTML'
                    )

                # Пытаемся отправить сообщение с HTML
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=message_text,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
                success_count += 1

                # Пауза чтобы не превысить лимиты Telegram
                if user_count % 20 == 0:
                    await asyncio.sleep(0.5)

            except Exception as e:
                failed_count += 1
                error_msg = str(e)
                if "Forbidden" in error_msg:
                    failed_users.append(f"{user_id}: пользователь заблокировал бота")
                elif "Chat not found" in error_msg:
                    failed_users.append(f"{user_id}: чат не найден")
                else:
                    failed_users.append(f"{user_id}: {error_msg[:30]}")

        # Финальный отчет о рассылке
        total_users = len(data['users'])
        success_percent = round(success_count / total_users * 100, 1) if total_users > 0 else 0

        # Создаем отчет
        report = f"""📊 <b>РАССЫЛКА ЗАВЕРШЕНА!</b>

🎯 <b>Результаты:</b>
✅ Успешно доставлено: <b>{success_count}</b>
❌ Не удалось отправить: <b>{failed_count}</b>
👥 Всего получателей: <b>{total_users}</b>

📈 <b>Эффективность:</b> <code>{success_percent}%</code>"""

        if failed_users:
            report += "\n\n<b>📋 Основные ошибки:</b>\n"
            for error in failed_users[:5]:
                report += f"<code>{error}</code>\n"

        # Обновляем статусное сообщение
        await status_msg.edit_text(report, parse_mode='HTML')


# ОБРАБОТКА ЛИЧНОГО СООБЩЕНИЯ
async def process_private_message(update: Update, context: CallbackContext):
    if context.user_data.get("sending_private"):
        text = update.message.text
        context.user_data["sending_private"] = False

        try:
            # Пытаемся разделить ID и сообщение
            parts = text.split(' ', 1)
            if len(parts) != 2:
                await update.message.reply_text(
                    "❌ <b>НЕВЕРНЫЙ ФОРМАТ!</b>\n\n"
                    "📌 <b>Правильный формат:</b>\n"
                    "<code>ID_ПОЛЬЗОВАТЕЛЯ СООБЩЕНИЕ</code>",
                    parse_mode='HTML'
                )
                return

            target_id = parts[0].strip()
            message_text = parts[1].strip()

            # Проверяем существует ли пользователь
            if target_id not in data["users"]:
                await update.message.reply_text(
                    f"❌ <b>ПОЛЬЗОВАТЕЛЬ НЕ НАЙДЕН!</b>\n\n"
                    f"Пользователь с ID <code>{target_id}</code> не найден в базе данных.",
                    parse_mode='HTML'
                )
                return

            # Пытаемся отправить сообщение
            try:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text=message_text,
                    parse_mode='HTML'
                )

                user_data = data["users"][target_id]
                await update.message.reply_text(
                    f"✅ <b>СООБЩЕНИЕ ОТПРАВЛЕНО!</b>\n"
                    f"👤 {user_data['name']} (<code>{target_id}</code>)",
                    parse_mode='HTML'
                )

            except Exception as e:
                await update.message.reply_text(f"❌ <b>ОШИБКА:</b> {str(e)}", parse_mode='HTML')

        except Exception as e:
            await update.message.reply_text(f"❌ <b>ОШИБКА:</b> {str(e)}", parse_mode='HTML')


# ОБРАБОТКА АДМИНСКИХ ВВОДОВ (ТЕКСТ)
async def handle_admin_input(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    text = update.message.text

    # Кнопка отмены, если админ передумал что-то вводить
    if text.lower() == "отмена":
        # Сброс всех состояний
        context.user_data["creating_promo"] = False
        context.user_data["banning_user"] = False
        context.user_data["unbanning_user"] = False
        context.user_data["broadcasting"] = False
        context.user_data["sending_private"] = False
        context.user_data["uploading_accounts"] = False
        context.user_data["adding_channel"] = False
        context.user_data["deleting_channel"] = False
        context.user_data["adding_admin"] = False
        
        await update.message.reply_text("❌ Действие отменено.", reply_markup=menu(user_id))
        # Возвращаем панель
        await update.message.reply_text("👑 <b>Админ панель v2.0</b>", parse_mode='HTML', reply_markup=admin_kb_main(user_id))
        return

    # Создание промо
    if context.user_data.get("creating_promo"):
        parts = text.split()
        if len(parts) != 3:
            await update.message.reply_text("❌ Неверный формат! Попробуйте снова или напишите 'отмена'.")
            return

        code = parts[0].upper()
        try:
            reward = int(parts[1])
            max_uses = int(parts[2])
        except:
            await update.message.reply_text("❌ Ошибка в числах!")
            return

        if code in data["promocodes"]:
            await update.message.reply_text("❌ Промокод уже существует!")
            return

        data["promocodes"][code] = {
            "reward": reward,
            "max_uses": max_uses,
            "used": 0
        }
        save()

        await update.message.reply_text(f"✅ Промокод {code} создан!")
        context.user_data["creating_promo"] = False

    # Бан юзера
    elif context.user_data.get("banning_user"):
        user_to_ban = text.strip()
        if user_to_ban in data.get("banned_users", []):
            await update.message.reply_text("❌ Пользователь уже забанен!")
        else:
            if "banned_users" not in data:
                data["banned_users"] = []
            data["banned_users"].append(user_to_ban)
            save()
            await update.message.reply_text(f"✅ Пользователь {user_to_ban} забанен!")
        context.user_data["banning_user"] = False

    # Разбан юзера
    elif context.user_data.get("unbanning_user"):
        user_to_unban = text.strip()
        if user_to_unban not in data.get("banned_users", []):
            await update.message.reply_text("❌ Пользователь не был забанен!")
        else:
            data["banned_users"].remove(user_to_unban)
            save()
            await update.message.reply_text(f"✅ Пользователь {user_to_unban} разбанен!")
        context.user_data["unbanning_user"] = False

    # Рассылка
    elif context.user_data.get("broadcasting"):
        await process_broadcast(update, context)

    # ЛС
    elif context.user_data.get("sending_private"):
        await process_private_message(update, context)

    # Управление каналами
    elif context.user_data.get("adding_channel"):
        channel = text.strip()
        if channel in data.get("channels", []):
             await update.message.reply_text("❌ Канал уже в списке!")
        else:
            # Важно: проверить, есть ли бот в канале
            try:
                chat = await context.bot.get_chat(channel)
                # Если прошло без ошибок, значит канал найден
                if "channels" not in data: data["channels"] = []
                data["channels"].append(channel)
                save()
                await update.message.reply_text(f"✅ Канал {channel} ({chat.title}) добавлен в список обязательных!")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: Бот не нашел канал или не является админом!\nТекст ошибки: {e}")
        
        context.user_data["adding_channel"] = False
        
    elif context.user_data.get("deleting_channel"):
        channel = text.strip()
        if channel in data.get("channels", []):
            data["channels"].remove(channel)
            save()
            await update.message.reply_text(f"✅ Канал {channel} удален из списка!")
        else:
            await update.message.reply_text("❌ Канал не найден в списке!")
        context.user_data["deleting_channel"] = False

    # Управление админами
    elif context.user_data.get("adding_admin"):
        try:
            new_admin_id = int(text.strip())
            str_id = str(new_admin_id)
            if new_admin_id in SUPER_ADMIN_IDS or str_id in data.get("admins", {}):
                await update.message.reply_text("❌ Этот пользователь уже админ!")
            else:
                if "admins" not in data: data["admins"] = {}
                data["admins"][str_id] = {
                    "permissions": DEFAULT_PERMISSIONS.copy(),
                    "added_by": user_id,
                    "date": datetime.now().isoformat()
                }
                save()
                await update.message.reply_text(f"✅ Пользователь {new_admin_id} добавлен как админ.\nНастройте его права в меню '🛡 Админы'.")
        except ValueError:
             await update.message.reply_text("❌ ID должен быть числом!")
        
        context.user_data["adding_admin"] = False


# ОБРАБОТКА ОТЗЫВА
async def process_review(update: Update, context: CallbackContext):
    if context.user_data.get("leaving_review"):
        text = update.message.text
        context.user_data["leaving_review"] = False

        user = update.effective_user
        user_id = str(user.id)

        data["reviews"] = data.get("reviews", []) + [{
            "user_id": user_id,
            "user_name": user.full_name,
            "text": text,
            "date": datetime.now().isoformat()
        }]
        save()

        await update.message.reply_text("⭐ <b>Спасибо за ваш отзыв!</b>\n\nВаш отзыв успешно сохранен.",
                                        parse_mode='HTML', reply_markup=menu(user.id))


# ОБРАБОТКА ДОКУМЕНТОВ
async def handle_document(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    if not context.user_data.get("uploading_accounts"):
        return

    document = update.message.document

    if document.file_name.endswith('.txt'):
        try:
            file = await document.get_file()
            await file.download_to_drive('temp_accounts.txt')

            with open('temp_accounts.txt', 'r', encoding='utf-8') as f:
                content = f.read()

            accounts = [line.strip() for line in content.split('\n') if ':' in line]
            added = 0

            for account in accounts:
                if ':' in account:
                    data["accounts"].append(account)
                    added += 1

            save()
            context.user_data["uploading_accounts"] = False
            
            # Очистка временного файла
            try:
                os.remove('temp_accounts.txt')
            except:
                pass

            await update.message.reply_text(f"✅ Загружено {added} аккаунтов!")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
            context.user_data["uploading_accounts"] = False
    else:
        await update.message.reply_text("❌ Пожалуйста, отправьте файл в формате .txt")


# ГЛАВНЫЙ ОБРАБОТЧИК ТЕКСТА
async def text_handler(update: Update, context: CallbackContext):
    text = update.message.text
    user = update.effective_user
    user_id = str(user.id)

    if user_id in data.get("banned_users", []):
        await update.message.reply_text("❌ Вы заблокированы в этом боте")
        return

    if BOT_STOPPED and not is_admin(user.id):
        await update.message.reply_text("❌ Бот временно остановлен администратором")
        return

    # Сначала проверяем состояния ввода (для админов и юзеров)
    if context.user_data.get("waiting_promo"):
        await process_promocode(update, context)
        return

    if context.user_data.get("leaving_review"):
        await process_review(update, context)
        return

    # Состояния админа (ввод данных после нажатия инлайн кнопки)
    if is_admin(user.id) and (
            context.user_data.get("creating_promo") or
            context.user_data.get("banning_user") or
            context.user_data.get("unbanning_user") or
            context.user_data.get("broadcasting") or
            context.user_data.get("sending_private") or
            context.user_data.get("adding_channel") or
            context.user_data.get("deleting_channel") or
            context.user_data.get("adding_admin")
    ):
        await handle_admin_input(update, context)
        return

    # Обычное меню пользователя
    if text == "🎮 Получить аккаунт":
        await get_account(update, context)
    elif text == "📜 История":
        await account_history(update, context)
    elif text == "💬 Поддержка":
        await support(update, context)
    elif text == "👤 Мой профиль":
        await profile(update, context)
    elif text == "💎 Обменять монеты":
        await exchange_coins(update, context)
    elif text == "🎟 Промокод":
        await activate_promocode(update, context)
    elif text == "📢 Канал":
        # Динамический список для кнопки
        chans = data.get("channels", [])
        if not chans:
            await update.message.reply_text("📢 Следите за новостями!", reply_markup=menu(user.id))
        else:
            await update.message.reply_text(f"📢 Наши каналы: {', '.join(chans)}", reply_markup=menu(user.id))
    elif text == "⭐ Отзывы":
        await reviews_menu(update, context)
    elif text == "✅ Проверить подписку":
        await check_subscription(update, context)
    elif text == "ℹ️ FAQ":
        await faq(update, context)
    elif text == "👑 Админ" and is_admin(user.id):
        await panel_command(update, context)
    else:
        await update.message.reply_text("Выберите действие из меню:", reply_markup=menu(user.id))


# ЗАПУСК
def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    logger = logging.getLogger(__name__)

    print("=" * 50)
    print("⚡ БОТ ЗАПУСКАЕТСЯ...")
    print("=" * 50)
    print(f"📱 Команда для запуска: /start")
    print(f"🔧 Админ команда: /panel")
    print(f"🗑 Админ команда для удаления отзыва: /delete_review НОМЕР")
    print(f"ℹ️ Админ команда: /info ID")
    print(f"👑 Супер-Админы: {SUPER_ADMIN_IDS}")
    print(f"🛡 Доп. Админов: {len(data.get('admins', {}))}")
    print(f"⏸ Статус бота: {'ОСТАНОВЛЕН' if BOT_STOPPED else 'РАБОТАЕТ'}")
    print(f"📊 Пользователей в базе: {len(data['users'])}")
    print(f"📦 Аккаунтов доступно: {len(data['accounts'])}")
    print("=" * 50)
    print("✅ Ожидание подключения к Telegram...")

    try:
        # Создаем application
        application = Application.builder().token(TOKEN).build()

        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("panel", panel_command))
        application.add_handler(CommandHandler("info", user_info))
        application.add_handler(CommandHandler("delete_review", delete_review))
        
        # ЕДИНЫЙ ОБРАБОТЧИК CALLBACK (и админ, и юзер)
        application.add_handler(CallbackQueryHandler(main_callback_handler))
        
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

        # Запускаем бота
        print("🟢 Бот запущен и слушает сообщения...")
        print("🔄 Для остановки нажмите Ctrl+C")
        print("=" * 50)

        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            timeout=30
        )

    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем (Ctrl+C)")
    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")
        logger.error(f"Ошибка при запуске бота: {e}")
        input("Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()