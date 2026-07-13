"""Пакетно обновляет Wordstat в Google Sheets без превышения часовой квоты."""

import os
import time
from datetime import datetime, timezone

import gspread
import requests
from dateutil.relativedelta import relativedelta
from oauth2client.service_account import ServiceAccountCredentials

# --- НАСТРОЙКИ ---
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")
GOOGLE_CREDS_FILE = "google_creds.json"
GOOGLE_SHEET_NAME = "TrendStat DB"

REGION_CODE = "225"
REGION_NAME = "Россия"
DEVICE_CODE = "DEVICE_ALL"
DEVICE_NAME = "Все устройства"
SOURCE_NAME = "Wordstat"

HISTORY_FROM_DATE = "2023-01-01T00:00:00Z"
MAX_REQUESTS_PER_RUN = 90
SAVE_EVERY_REQUESTS = 25
REQUEST_DELAY_SECONDS = 1
REQUEST_TIMEOUT_SECONDS = 45

if not YANDEX_API_KEY or not FOLDER_ID:
    raise RuntimeError("Не заданы обязательные переменные YANDEX_API_KEY и FOLDER_ID")


def date_key(value):
    """Приводит дату Sheets/API к YYYY-MM-DD для надежного сравнения."""
    return str(value).strip()[:10]


# --- 1. Подключение к Google Таблицам ---
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_FILE, scope)
client = gspread.authorize(creds)
spreadsheet = client.open(GOOGLE_SHEET_NAME)
data_sheet = spreadsheet.worksheet("Data")
config_sheet = spreadsheet.worksheet("Config")

# --- 2. Читаем конфиг и уже собранные данные ---
config_data = config_sheet.get_all_values()[1:]
phrases_config = [
    {"phrase": row[0].strip(), "tag": row[1].strip() if len(row) > 1 else ""}
    for row in config_data
    if row and row[0].strip()
]

existing_data = data_sheet.get_all_values()[1:]
existing_signatures = set()
phrases_with_history = set()
for row in existing_data:
    if len(row) < 7:
        continue
    signature = (date_key(row[0]), row[1], row[2], row[4], row[6])
    existing_signatures.add(signature)
    if row[1] == REGION_NAME and row[2] == DEVICE_NAME and row[6] == SOURCE_NAME:
        phrases_with_history.add(row[4])

# --- 3. Период: последний полностью завершившийся месяц ---
now_utc = datetime.now(timezone.utc)
current_month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
last_month_start = current_month_start - relativedelta(months=1)
last_month_end = current_month_start - relativedelta(days=1)
target_date_key = last_month_start.strftime("%Y-%m-01")
target_from_date = last_month_start.strftime("%Y-%m-01T00:00:00Z")
to_date = last_month_end.strftime("%Y-%m-%dT00:00:00Z")

url = "https://searchapi.api.cloud.yandex.net/v2/wordstat/dynamics"
headers = {
    "Authorization": f"Api-Key {YANDEX_API_KEY}",
    "Content-Type": "application/json",
}

rows_to_append = []
request_count = 0
saved_rows = 0
stopped_by_limit = False
stopped_by_quota = False


def save_progress():
    """Сохраняет накопленные строки небольшими безопасными порциями."""
    global rows_to_append, saved_rows
    if not rows_to_append:
        return
    print(f"💾 Записываем {len(rows_to_append)} строк в Google Sheets...")
    data_sheet.append_rows(rows_to_append)
    saved_rows += len(rows_to_append)
    rows_to_append = []


print(
    f"🚀 Фраз в Config: {len(phrases_config)}. "
    f"Целевой месяц: {target_date_key}. Лимит запуска: {MAX_REQUESTS_PER_RUN}."
)

# --- 4. Сбор данных ---
try:
    for item in phrases_config:
        phrase, tag = item["phrase"], item["tag"]
        check_signature = (
            target_date_key,
            REGION_NAME,
            DEVICE_NAME,
            phrase,
            SOURCE_NAME,
        )
        if check_signature in existing_signatures:
            continue

        if request_count >= MAX_REQUESTS_PER_RUN:
            stopped_by_limit = True
            break

        # Новым фразам загружаем историю, известным — только последний месяц.
        request_from_date = (
            target_from_date if phrase in phrases_with_history else HISTORY_FROM_DATE
        )
        body = {
            "phrase": phrase,
            "period": "PERIOD_MONTHLY",
            "folderId": FOLDER_ID,
            "fromDate": request_from_date,
            "toDate": to_date,
            "regions": [REGION_CODE],
            "devices": [DEVICE_CODE],
        }

        response = requests.post(
            url,
            json=body,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        request_count += 1

        if response.status_code == 200:
            added_count = 0
            for point in response.json().get("results", []):
                point_date = point.get("date")
                if not point_date:
                    continue
                signature = (
                    date_key(point_date),
                    REGION_NAME,
                    DEVICE_NAME,
                    phrase,
                    SOURCE_NAME,
                )
                if signature in existing_signatures:
                    continue
                rows_to_append.append(
                    [
                        point_date,
                        REGION_NAME,
                        DEVICE_NAME,
                        tag,
                        phrase,
                        point.get("count", 0),
                        SOURCE_NAME,
                    ]
                )
                existing_signatures.add(signature)
                added_count += 1
            print(f"✅ [{request_count}/{MAX_REQUESTS_PER_RUN}] '{phrase}': +{added_count}")
        elif response.status_code == 429:
            print("⚠️ Яндекс вернул 429. Сохраняем прогресс; следующий запуск продолжит.")
            stopped_by_quota = True
            break
        elif response.status_code in (401, 403):
            raise RuntimeError(
                f"Яндекс отклонил авторизацию ({response.status_code}): {response.text}"
            )
        else:
            print(f"❌ [{request_count}] '{phrase}': HTTP {response.status_code}")

        if request_count % SAVE_EVERY_REQUESTS == 0:
            save_progress()
        time.sleep(REQUEST_DELAY_SECONDS)
finally:
    # Выполняется и при сетевой/непредвиденной ошибке.
    save_progress()

remaining = sum(
    (
        target_date_key,
        REGION_NAME,
        DEVICE_NAME,
        item["phrase"],
        SOURCE_NAME,
    )
    not in existing_signatures
    for item in phrases_config
)

print(
    f"🏁 Запросов: {request_count}; сохранено строк: {saved_rows}; "
    f"фраз без данных за {target_date_key}: {remaining}."
)
if stopped_by_limit:
    print("⏭️ Достигнут безопасный лимит запуска. Продолжение — в следующий час.")
elif stopped_by_quota:
    print("⏭️ Часовая квота исчерпана. Продолжение — по расписанию.")
elif remaining:
    print("⚠️ Некоторые фразы не вернули целевой месяц и будут проверены снова.")
else:
    print("✅ Все фразы обновлены до последнего полного месяца.")