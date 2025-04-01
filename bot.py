import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler,
    MessageHandler, Filters, ConversationHandler, CallbackContext
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()
TOKEN = os.getenv("TOKEN")
# ALLOWED_USERS – список разрешённых ID пользователей (указывайте через запятую)
ALLOWED_USERS = [int(uid.strip()) for uid in os.getenv("ALLOWED_USERS", "").split(",") if uid.strip()]
CREDS_FILE = os.getenv("CREDS_FILE", "credentials.json")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "fn_table")

# Этапы диалога
SELECT_DURATION, ENTER_ORG = range(2)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def restricted(func):
    """Декоратор для ограничения доступа к функциям бота по ID пользователя."""
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USERS:
            if update.message:
                update.message.reply_text("У вас нет доступа к этому боту.")
            elif update.callback_query:
                update.callback_query.answer("У вас нет доступа к этому боту.")
            return ConversationHandler.END
        return func(update, context, *args, **kwargs)
    return wrapped

def get_sheet_by_duration(duration):
    """Возвращает нужный лист в таблице в зависимости от длительности ключа.
       Для 15-месячных кодов используется первый лист,
       для 36-месячных – второй лист."""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open(SPREADSHEET_NAME)
    if duration == '15':
        return spreadsheet.get_worksheet(0)  # первый лист для 15-месячных
    elif duration == '36':
        return spreadsheet.get_worksheet(1)  # второй лист для 36-месячных
    else:
        return None

@restricted
def main_menu(update: Update, context: CallbackContext):
    """Отображает главное меню с кнопкой 'Получить ключ'."""
    keyboard = [
        [InlineKeyboardButton("Получить ключ", callback_data='get_key')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        update.callback_query.edit_message_text(
            text="Добро пожаловать! Выберите действие:", reply_markup=reply_markup
        )
    else:
        update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)

@restricted
def start(update: Update, context: CallbackContext):
    """Обработка команды /start: вывод главного меню."""
    main_menu(update, context)

@restricted
def menu_handler(update: Update, context: CallbackContext):
    """Обработка нажатия на кнопку главного меню."""
    query = update.callback_query
    query.answer()
    
    if query.data == 'get_key':
        # Показываем выбор длительности
        keyboard = [
            [InlineKeyboardButton("15 месяцев", callback_data='15')],
            [InlineKeyboardButton("36 месяцев", callback_data='36')],
            [InlineKeyboardButton("Отмена", callback_data='cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text="Выберите срок ключа:", reply_markup=reply_markup)
        return SELECT_DURATION
    elif query.data == 'cancel':
        query.edit_message_text(text="Операция отменена.")
        main_menu(update, context)
        return ConversationHandler.END

@restricted
def select_duration(update: Update, context: CallbackContext):
    """Обработка выбора длительности ключа."""
    query = update.callback_query
    query.answer()
    duration = query.data
    
    if duration not in ['15', '36']:
        query.edit_message_text(text="Неверный выбор.")
        main_menu(update, context)
        return ConversationHandler.END

    context.user_data['duration'] = duration
    query.edit_message_text(
        text=f"Вы выбрали ключ на {duration} месяцев.\nВведите название организации и РТУ:"
    )
    return ENTER_ORG

@restricted
def receive_org(update: Update, context: CallbackContext):
    """Получение названия организации и выдача ключа с блокировкой."""
    org_name = update.message.text.strip()
    duration = context.user_data.get('duration')

    if not duration:
        update.message.reply_text("Произошла ошибка. Попробуйте снова.")
        main_menu(update, context)
        return ConversationHandler.END

    sheet = get_sheet_by_duration(duration)
    if not sheet:
        update.message.reply_text("Ошибка при выборе таблицы.")
        main_menu(update, context)
        return ConversationHandler.END

    # Получаем все данные из листа
    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        update.message.reply_text("Нет доступных кодов.")
        main_menu(update, context)
        return ConversationHandler.END

    data = all_values[1:]

    for i, row in enumerate(data):
        if len(row) < 2 or not row[1].strip():  # Проверяем, пустая ли ячейка организации
            key = row[0].strip() if row[0].strip() else None
            if key:
                row_num = i + 2  # Строка в таблице (начинается с 1, заголовок – первая)
                
                # Делаем быструю блокировку, чтобы избежать одновременной выдачи
                sheet.update_cell(row_num, 2, "В обработке")  

                # Теперь выдаем ключ пользователю
                sheet.update_cell(row_num, 2, org_name)  # Фиксируем организацию
                update.message.reply_text(f"Ваш ключ: {key}\nОрганизация: {org_name}")
                main_menu(update, context)
                return ConversationHandler.END

    update.message.reply_text("Свободных ключей не найдено.")
    main_menu(update, context)
    return ConversationHandler.END

@restricted
def cancel(update: Update, context: CallbackContext):
    """Обработка команды отмены."""
    update.message.reply_text('Операция отменена.')
    main_menu(update, context)
    return ConversationHandler.END

def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern='^(get_key|cancel)$')],
        states={
            SELECT_DURATION: [CallbackQueryHandler(select_duration, pattern='^(15|36)$')],
            ENTER_ORG: [MessageHandler(Filters.text & ~Filters.command, receive_org)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True
    )

    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(conv_handler)

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
