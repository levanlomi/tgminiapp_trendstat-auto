import requests
from bs4 import BeautifulSoup
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- НАСТРОЙКИ ---
URL = "https://greenway.icnet.ru/cars-sales-actual-russia.html"
GOOGLE_CREDS_FILE = "google_creds.json"
GOOGLE_SHEET_NAME = "TrendStat DB"

# Подключение к Гугл Таблицам
print("⏳ Подключаемся к Google Таблицам...")
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_FILE, scope)
client = gspread.authorize(creds)
sales_sheet = client.open(GOOGLE_SHEET_NAME).worksheet("Sales_Data")

# Читаем уже существующие данные
existing_data = sales_sheet.get_all_values()[1:]
existing_sigs = set()
for row in existing_data:
    if len(row) >= 3:
        # Уникальная подпись: (Дата, Категория)
        existing_sigs.add((row[0], row[1]))

print("⏳ Скачиваем данные с сайта...")
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'}
response = requests.get(URL, headers=headers)
response.encoding = 'utf-8'
soup = BeautifulSoup(response.text, 'html.parser')
scripts = soup.find_all('script')

# Словарь для перевода русских месяцев в числа
months_map = {'Янв': 1, 'Фев': 2, 'Мар': 3, 'Апр': 4, 'Май': 5, 'Июн': 6, 'Июл': 7, 'Авг': 8, 'Сен': 9, 'Окт': 10, 'Ноя': 11, 'Дек': 12}

def parse_chart(scripts, title_keyword):
    for script in scripts:
        if script.string and title_keyword in script.string:
            # Ищем массив данных внутри скрипта
            match = re.search(r"google\.visualization\.arrayToDataTable\(\[(.*?)\]\);", script.string, re.DOTALL)
            if match:
                rows_raw = re.findall(r"\[(.*?)\]", match.group(1))
                
                # Парсим года из заголовка (например, ['Мес', '2024', '2025', '2026'])
                headers = [p.strip().strip("'\"") for p in rows_raw[0].split(',')]
                years = [h for h in headers if h.isdigit() and len(h) == 4]
                
                data = {}
                for row in rows_raw[1:]:
                    parts = [p.strip() for p in row.split(',')]
                    month_str = parts[0].strip("'\"")
                    
                    if month_str in months_map:
                        month_num = months_map[month_str]
                        for i, year in enumerate(years):
                            if i + 1 < len(parts):
                                val_str = parts[i+1]
                                # Если данные за этот месяц есть (не null)
                                if val_str != 'null' and val_str != '':
                                    try:
                                        date_str = f"01.{month_num:02d}.{year}"
                                        count = int(float(val_str) * 1000)
                                        data[date_str] = count
                                    except ValueError:
                                        pass
                return data
    return {}

print("🔍 Собираем 'Новые авто'...")
new_cars = parse_chart(scripts, 'Динамика продаж новых автомобилей')
print(f"   Найдено месяцев: {len(new_cars)}")

print("🔍 Собираем 'Б/У авто'...")
used_cars = parse_chart(scripts, 'Динамика продаж авто с пробегом')
print(f"   Найдено месяцев: {len(used_cars)}")

# Объединяем и готовим к записи
rows_to_append = []
all_dates = set(new_cars.keys()).union(set(used_cars.keys()))

for date_str in sorted(all_dates, key=lambda x: (x.split('.')[2], x.split('.')[1])):
    n_val = new_cars.get(date_str, 0)
    u_val = used_cars.get(date_str, 0)
    t_val = n_val + u_val
    
    if n_val > 0 and (date_str, 'New') not in existing_sigs:
        rows_to_append.append([date_str, 'New', n_val, 'Autostat'])
    
    if u_val > 0 and (date_str, 'Used') not in existing_sigs:
        rows_to_append.append([date_str, 'Used', u_val, 'Autostat'])
        
    if t_val > 0 and (date_str, 'Total') not in existing_sigs:
        rows_to_append.append([date_str, 'Total', t_val, 'Autostat'])

# Массовая запись в таблицу
if rows_to_append:
    print(f"\n🚀 Найдено новых записей для добавления: {len(rows_to_append)}")
    sales_sheet.append_rows(rows_to_append)
    print("✅ Данные успешно записаны в Google Таблицу!")
else:
    print("\n✅ База актуальна, новых данных нет.")