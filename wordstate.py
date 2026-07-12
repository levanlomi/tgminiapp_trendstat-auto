##ТАЩИТ ДАННЫЕ ИЗ ВОРДСТАТА В ГУГЛЩИТС, ОБНОВЛЯЕТ ДАННЫЕ БЕЗ ЗАТИРАНИЯ##

import os
import requests
import gspread

from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
from dateutil.relativedelta import relativedelta

# --- НАСТРОЙКИ ---
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")
GOOGLE_CREDS_FILE = "google_creds.json"
GOOGLE_SHEET_NAME = "TrendStat DB"

# Коды регионов Яндекс
REGIONS = {
    "225": "Россия"
}

# Типы устройств (ИСПРАВЛЕНО: DEVICE_PHONE вместо DEVICE_MOBILE)
DEVICES = {
    "DEVICE_ALL": "Все устройства"
}

# --- 1. Подключение к Google Таблицам ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_FILE, scope)
client = gspread.authorize(creds)

sheet = client.open(GOOGLE_SHEET_NAME)
data_sheet = sheet.worksheet("Data")
config_sheet = sheet.worksheet("Config")

# --- 2. Получение настроек и текущих данных ---
config_data = config_sheet.get_all_values()[1:]
phrases_config = [{"phrase": row[0], "tag": row[1] if len(row) > 1 else ""} for row in config_data if row[0].strip()]
print(f"Загружено фраз из конфига: {len(phrases_config)}")

# Читаем уже собранные данные, чтобы знать, где мы остановились
existing_data = data_sheet.get_all_values()[1:]
existing_signatures = set()
for row in existing_data:
    if len(row) >= 7:
        # Сигнатура: (Дата, Регион, Устройство, Фраза, Источник)
        sig = (row[0], row[1], row[2], row[4], row[6])
        existing_signatures.add(sig)

# --- 3. Настройка дат ---
from_date = "2023-01-01T00:00:00Z"
today = datetime.now()
last_day_of_prev_month = today.replace(day=1) - relativedelta(days=1)
to_date = last_day_of_prev_month.strftime("%Y-%m-%dT00:00:00Z")

print(f"Период сбора: {from_date} — {to_date}")

# --- 4. Сбор данных ---
url = "https://searchapi.api.cloud.yandex.net/v2/wordstat/dynamics"
headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}

rows_to_append = []
quota_exceeded = False # Флаг, если лимит кончится

# Мы оборачиваем цикл в try...finally, чтобы данные записались ДАЖЕ если случится ошибка
try:
    for item in phrases_config:
        if quota_exceeded: break
        phrase, tag = item["phrase"], item["tag"]
        
        for region_code, region_name in REGIONS.items():
            if quota_exceeded: break
            for device_code, device_name in DEVICES.items():
                
                # ПРОПУСК: Если данные по этой фразе/региону/девайсу уже есть (хотя бы за одну дату), пропускаем весь запрос
                # Это экономит наши 100 запросов в час
                check_sig = (from_date, region_name, device_name, phrase, "Wordstat")
                if check_sig in existing_signatures:
                    continue

                print(f"Запрашиваем: '{phrase}' | {region_name} | {device_name} ...")
                
                body = {
                    "phrase": phrase,
                    "period": "PERIOD_MONTHLY",
                    "fromDate": from_date,
                    "toDate": to_date,
                    "regions": [region_code],
                    "devices": [device_code],
                    "folderId": FOLDER_ID
                }

                response = requests.post(url, json=body, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", []) # ИСПРАВЛЕНО: берем results
                    
                    added_count = 0
                    for point in results:
                        d_p, count = point.get("date"), point.get("count", 0)
                        sig = (d_p, region_name, device_name, phrase, "Wordstat")
                        
                        if sig not in existing_signatures:
                            rows_to_append.append([d_p, region_name, device_name, tag, phrase, count, "Wordstat"])
                            existing_signatures.add(sig)
                            added_count += 1
                    
                    if added_count > 0:
                        print(f"  -> Найдено новых месяцев: {added_count}")
                    
                    time.sleep(1.2) # Пауза между запросами

                elif response.status_code == 429:
                    print("!!! ЛИМИТ ЗАПРОСОВ ЯНДЕКСА ИСЧЕРПАН (429) !!!")
                    quota_exceeded = True
                    break
                else:
                    print(f"  [ОШИБКА] {response.status_code}: {response.text}")

finally:
    # --- 5. Запись результатов (выполнится в любом случае) ---
    if rows_to_append:
        print(f"\nЗаписываем {len(rows_to_append)} новых строк в Google Таблицу...")
        data_sheet.append_rows(rows_to_append)
        print("✅ Данные успешно сохранены в таблицу!")
    else:
        print("\n✅ Новых данных для добавления не найдено.")
    
    if quota_exceeded:
        print("ВНИМАНИЕ: Скрипт собрал часть данных и остановился из-за лимита. Запустите его через час, чтобы продолжить.")