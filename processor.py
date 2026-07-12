##ЗАБИРАЕТ ДАННЫЕ ИЗ ГУГЛ ЩИТСА И ГОТОВИТ ИХ К ФРОНТЭНДУ + ОБРАЩАЕМСЯ С ИИ ДЛЯ СВОДОК##

import os
import requests
import gspread

from oauth2client.service_account import ServiceAccountCredentials
import json
import pandas as pd
from datetime import datetime

# --- НАСТРОЙКИ ---
GOOGLE_CREDS_FILE = "google_creds.json"
GOOGLE_SHEET_NAME = "TrendStat DB"

YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")

# --- 1. ФУНКЦИИ ---
def calculate_yoy(current_val, previous_val):
    if previous_val and previous_val > 0:
        return float(round(((current_val - previous_val) / previous_val) * 100, 1))
    return 0.0

def get_ytd_sum(df, year, max_month, column='Count'):
    if df.empty: return 0.0
    return float(df[(df['Date'].dt.year == year) & (df['Date'].dt.month <= max_month)][column].sum())

# Универсальная функция для ИИ (чтобы не дублировать код)
def get_ai_insight(prompt_text, api_key, folder_id):
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"}
    body = {
        "modelUri": f"gpt://{folder_id}/yandexgpt/latest", # PRO-модель
        "completionOptions": {"stream": False, "temperature": 0.5, "maxTokens": "2000"},
        "messages": [
            {"role": "system", "text": "Ты — ведущий аналитик авторынка России. Твоя задача — давать профессиональные макроэкономические комментарии на основе цифр."},
            {"role": "user", "text": prompt_text}
        ]
    }
    try:
        response = requests.post(url, headers=headers, json=body)
        if response.status_code == 200:
            return response.json()['result']['alternatives'][0]['message']['text']
        else:
            return "Не удалось сгенерировать ИИ-сводку."
    except:
        return "Аналитика временно недоступна."

# --- 2. ПОДКЛЮЧЕНИЕ И ЗАГРУЗКА ДАННЫХ ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_FILE, scope)
client = gspread.authorize(creds)
sheet = client.open(GOOGLE_SHEET_NAME)

ws_values = sheet.worksheet("Data").get_all_values()
df_ws = pd.DataFrame(ws_values[1:], columns=ws_values[0])
df_ws = df_ws.loc[:, df_ws.columns.astype(str).str.strip() != '']
df_ws['Count'] = pd.to_numeric(df_ws['Count'])
df_ws['Date'] = pd.to_datetime(df_ws['Date']).dt.tz_localize(None).dt.to_period('M').dt.to_timestamp()

sales_values = sheet.worksheet("Sales_Data").get_all_values()
df_sales = pd.DataFrame(sales_values[1:], columns=sales_values[0])
df_sales = df_sales.loc[:, df_sales.columns.astype(str).str.strip() != '']
df_sales['Count'] = pd.to_numeric(df_sales['Count'])
df_sales['Date'] = pd.to_datetime(df_sales['Date'], dayfirst=True).dt.tz_localize(None).dt.to_period('M').dt.to_timestamp()

# --- 3. БАЗОВАЯ ФИЛЬТРАЦИЯ И СИНХРОНИЗАЦИЯ ДАТ ---
df_base = df_ws[(df_ws['Region'].astype(str).str.strip() == 'Россия') & (df_ws['Device'].astype(str).str.strip() == 'Все устройства')].copy()
df_base['Tag_clean'] = df_base['Tag'].astype(str).str.lower().str.replace(' ', '', regex=False).str.replace('.', '', regex=False)

# Находим последнюю дату, где есть И запросы (Вордстат), И продажи (Автостат)
max_ws_date = df_base['Date'].max()
max_sales_date = df_sales['Date'].max()
valid_last_date = min(max_ws_date, max_sales_date) # Выбираем "отстающий" месяц

# Отрезаем данные, которые "забегают вперед" (если Вордстат обогнал Автостат)
df_base = df_base[df_base['Date'] <= valid_last_date]
df_sales = df_sales[df_sales['Date'] <= valid_last_date]

print(f"📊 Дашборд синхронизирован по дате: {valid_last_date.strftime('%Y-%m-%d')}")

# --- 4. РАСЧЕТ ВЛАДКИ 1: ИНДУСТРИЯ ---
industry_monthly = df_base.groupby('Date')['Count'].sum().reset_index()
industry_sales = df_sales[df_sales['Category'] == 'Total'][['Date', 'Count']].sort_values('Date')
industry_sales.columns = ['Date', 'Sales_Count']
final_df = pd.merge(industry_monthly, industry_sales, on='Date', how='left')



last_date = industry_monthly['Date'].max()
curr_year = last_date.year
curr_month = last_date.month

yoy_growth = calculate_yoy(get_ytd_sum(industry_monthly, curr_year, curr_month), get_ytd_sum(industry_monthly, curr_year - 1, curr_month))
sales_yoy_growth = calculate_yoy(get_ytd_sum(final_df, curr_year, curr_month, 'Sales_Count'), get_ytd_sum(final_df, curr_year - 1, curr_month, 'Sales_Count'))
avg_interest_year = float(industry_monthly.tail(12)['Count'].mean())
final_df['Index'] = ((final_df['Sales_Count'] / final_df['Count']) * 100).round(1)

rolling_avg = final_df['Count'].rolling(window=12, min_periods=1).mean()
final_df['is_high_season'] = final_df['Count'] > (rolling_avg * 1.10)

# --- 5. РАСЧЕТ ВКЛАДКИ 2: АГРЕГАТОРЫ ---
# 5.1 Общие агрегаторы
agg_tags = ['авитоавто', 'автору', 'дром', 'классифайды', 'объявления']
df_agg_all = df_base[df_base['Tag_clean'].isin(agg_tags)]
agg_monthly = df_agg_all.groupby('Date')['Count'].sum().reset_index()

agg_avg_month = float(agg_monthly.tail(12)['Count'].mean()) if not agg_monthly.empty else 0.0
agg_ytd_sum = get_ytd_sum(agg_monthly, curr_year, curr_month)
ind_ytd_sum = get_ytd_sum(industry_monthly, curr_year, curr_month)

agg_share = int(round((agg_ytd_sum / ind_ytd_sum) * 100)) if ind_ytd_sum > 0 else 0
agg_yoy_growth = calculate_yoy(agg_ytd_sum, get_ytd_sum(agg_monthly, curr_year - 1, curr_month))

# 5.2 ТОП-3
df_avito = df_base[df_base['Tag_clean'] == 'авитоавто'].groupby('Date')['Count'].sum().reset_index()
df_autoru = df_base[df_base['Tag_clean'] == 'автору'].groupby('Date')['Count'].sum().reset_index()
df_drom = df_base[df_base['Tag_clean'] == 'дром'].groupby('Date')['Count'].sum().reset_index()

avito_ytd = get_ytd_sum(df_avito, curr_year, curr_month)
autoru_ytd = get_ytd_sum(df_autoru, curr_year, curr_month)
drom_ytd = get_ytd_sum(df_drom, curr_year, curr_month)

top3_sum = avito_ytd + autoru_ytd + drom_ytd

avito_share = int(round((avito_ytd / top3_sum) * 100)) if top3_sum > 0 else 34
autoru_share = int(round((autoru_ytd / top3_sum) * 100)) if top3_sum > 0 else 33
drom_share = int(round((drom_ytd / top3_sum) * 100)) if top3_sum > 0 else 33

# Корректировка округления (чтобы сумма была ровно 100%)
if top3_sum > 0:
    diff = 100 - (avito_share + autoru_share + drom_share)
    avito_share += diff

leader_map = {avito_ytd: 'Авито Авто', autoru_ytd: 'Авто.ру', drom_ytd: 'Дром'}
agg_leader = leader_map[max(avito_ytd, autoru_ytd, drom_ytd)] if top3_sum > 0 else "-"

# 5.3 Подготовка массивов для графиков
def get_values(df_source, dates_ref):
    if df_source.empty: return [0.0] * len(dates_ref)
    df_source = df_source.set_index('Date')
    return [float(df_source.loc[d, 'Count']) if d in df_source.index else 0.0 for d in dates_ref]

dates_ref = final_df['Date'].tolist()
agg_interest_list = get_values(agg_monthly, dates_ref)
avito_list = get_values(df_avito, dates_ref)
autoru_list = get_values(df_autoru, dates_ref)
drom_list = get_values(df_drom, dates_ref)

# --- 6. РАСЧЕТ ВКЛАДОК: ПОДДЕРЖАННЫЕ И НОВЫЕ АВТО ---
df_used = df_base[df_base['Tag_clean'] == 'бу']
df_new = df_base[df_base['Tag_clean'] == 'новые']
used_monthly = df_used.groupby('Date')['Count'].sum().reset_index()
new_monthly = df_new.groupby('Date')['Count'].sum().reset_index()

used_sales_monthly = df_sales[df_sales['Category'] == 'Used'].groupby('Date')['Count'].sum().reset_index()
new_sales_monthly = df_sales[df_sales['Category'] == 'New'].groupby('Date')['Count'].sum().reset_index()

def build_vehicle_segment(interest_monthly, sales_monthly):
    segment_df = pd.DataFrame({'Date': dates_ref})
    segment_df = segment_df.merge(interest_monthly, on='Date', how='left')
    segment_df = segment_df.merge(
        sales_monthly.rename(columns={'Count': 'Sales_Count'}), on='Date', how='left'
    )
    segment_df['Count'] = segment_df['Count'].fillna(0)
    segment_df['Sales_Count'] = segment_df['Sales_Count'].fillna(0)
    segment_df['Index'] = segment_df.apply(
        lambda r: round((r['Sales_Count'] / r['Count']) * 100, 1) if r['Count'] > 0 else 0.0,
        axis=1
    )
    rolling_avg_segment = segment_df['Count'].rolling(window=12, min_periods=1).mean()
    segment_df['is_high_season'] = segment_df['Count'] > (rolling_avg_segment * 1.10)
    return segment_df

used_final = build_vehicle_segment(used_monthly, used_sales_monthly)
new_final = build_vehicle_segment(new_monthly, new_sales_monthly)

used_avg_month = float(used_monthly.tail(12)['Count'].mean()) if not used_monthly.empty else 0.0
new_avg_month = float(new_monthly.tail(12)['Count'].mean()) if not new_monthly.empty else 0.0
used_ytd_sum = get_ytd_sum(used_monthly, curr_year, curr_month)
new_ytd_sum = get_ytd_sum(new_monthly, curr_year, curr_month)
vehicle_interest_ytd_sum = used_ytd_sum + new_ytd_sum
used_share = int(round((used_ytd_sum / vehicle_interest_ytd_sum) * 100)) if vehicle_interest_ytd_sum > 0 else 0
new_share = int(round((new_ytd_sum / vehicle_interest_ytd_sum) * 100)) if vehicle_interest_ytd_sum > 0 else 0
used_yoy_growth = calculate_yoy(used_ytd_sum, get_ytd_sum(used_monthly, curr_year - 1, curr_month))
new_yoy_growth = calculate_yoy(new_ytd_sum, get_ytd_sum(new_monthly, curr_year - 1, curr_month))
used_sales_yoy = calculate_yoy(
    get_ytd_sum(used_final, curr_year, curr_month, 'Sales_Count'),
    get_ytd_sum(used_final, curr_year - 1, curr_month, 'Sales_Count')
)
new_sales_yoy = calculate_yoy(
    get_ytd_sum(new_final, curr_year, curr_month, 'Sales_Count'),
    get_ytd_sum(new_final, curr_year - 1, curr_month, 'Sales_Count')
)

new_sales_list = get_values(new_sales_monthly, dates_ref)
used_sales_list = get_values(used_sales_monthly, dates_ref)
used_sales_pct = [
    round(u / (n + u) * 100, 1) if (n + u) > 0 else 0.0
    for n, u in zip(new_sales_list, used_sales_list)
]
new_sales_pct = [
    round(n / (n + u) * 100, 1) if (n + u) > 0 else 0.0
    for n, u in zip(new_sales_list, used_sales_list)
]

used_max_index = float(used_final['Index'].max()) if not used_final.empty else 0.0
used_current_index = float(used_final['Index'].iloc[-1]) if not pd.isna(used_final['Index'].iloc[-1]) else 0.0
used_max_index_limit = used_max_index * 1.1 if used_max_index > 0 else 1.0
used_ai_score = round((used_current_index / used_max_index_limit) * 10, 1) if used_max_index_limit > 0 else 0.0

new_max_index = float(new_final['Index'].max()) if not new_final.empty else 0.0
new_current_index = float(new_final['Index'].iloc[-1]) if not pd.isna(new_final['Index'].iloc[-1]) else 0.0
new_max_index_limit = new_max_index * 1.1 if new_max_index > 0 else 1.0
new_ai_score = round((new_current_index / new_max_index_limit) * 10, 1) if new_max_index_limit > 0 else 0.0

ytd_new_sales = get_ytd_sum(new_sales_monthly, curr_year, curr_month)
ytd_used_sales = get_ytd_sum(used_sales_monthly, curr_year, curr_month)
avg_used_sales_share = int(round((ytd_used_sales / (ytd_new_sales + ytd_used_sales)) * 100)) if (ytd_new_sales + ytd_used_sales) > 0 else 0
avg_new_sales_share = int(round((ytd_new_sales / (ytd_new_sales + ytd_used_sales)) * 100)) if (ytd_new_sales + ytd_used_sales) > 0 else 0

# --- 7. РАСЧЕТ ВКЛАДКИ: МАРКИ АВТО ---
def normalize_tag(tag):
    return str(tag).lower().replace(' ', '').replace('.', '')

BRAND_TAGS_RAW = [
    'Lada', 'Haval', 'Tenet', 'Geely', 'Changan', 'Belgee', 'Toyota', 'Jetour', 'Mazda',
    'Jaecoo', 'Chery', 'Exeed', 'Omoda', 'Solaris', 'Tank', 'Kia', 'Hyundai', 'XCITE',
    'BMW', 'GAC', 'Moskvitch', 'Li Auto', 'JAC', 'Voyah', 'UAZ', 'FAW', 'Volkswagen',
    'Nissan', 'Honda', 'Kaiyi', 'Jetta', 'Zeekr', 'Mercedes', 'GAZ', 'Livan', 'BAIC',
    'Audi', 'Chevrolet', 'Renault', 'Lexus', 'Tesla', 'Porsche'
]
BRAND_TAGS = [normalize_tag(b) for b in BRAND_TAGS_RAW]

BRAND_COUNTRY = {normalize_tag(k): v for k, v in {
    'Toyota': 'Япония', 'Mazda': 'Япония', 'Nissan': 'Япония', 'Honda': 'Япония', 'Lexus': 'Япония',
    'Kia': 'Южная Корея', 'Hyundai': 'Южная Корея',
    'Renault': 'Франция',
    'Chevrolet': 'США', 'Tesla': 'США',
    'Lada': 'Россия', 'Tenet': 'Россия', 'Solaris': 'Россия', 'XCITE': 'Россия',
    'Moskvitch': 'Россия', 'UAZ': 'Россия', 'GAZ': 'Россия',
    'Haval': 'Китай', 'Geely': 'Китай', 'Changan': 'Китай', 'Jetour': 'Китай', 'Jaecoo': 'Китай',
    'Chery': 'Китай', 'Exeed': 'Китай', 'Omoda': 'Китай', 'Tank': 'Китай', 'GAC': 'Китай',
    'Li Auto': 'Китай', 'JAC': 'Китай', 'Voyah': 'Китай', 'FAW': 'Китай', 'Kaiyi': 'Китай',
    'Zeekr': 'Китай', 'Livan': 'Китай', 'BAIC': 'Китай', 'Jetta': 'Китай',
    'BMW': 'Германия', 'Volkswagen': 'Германия', 'Mercedes': 'Германия', 'Audi': 'Германия',
    'Porsche': 'Германия',
    'Belgee': 'Беларусь',
}.items()}

BRAND_DISPLAY = {normalize_tag(b): b for b in BRAND_TAGS_RAW}
BRAND_DISPLAY.update({
    'bmw': 'BMW', 'gac': 'GAC', 'jac': 'JAC', 'uaz': 'UAZ', 'faw': 'FAW', 'gaz': 'GAZ',
    'baic': 'BAIC', 'xcite': 'XCITE', 'liauto': 'Li Auto',
})

COUNTRY_ORDER = ['Россия', 'Китай', 'Япония', 'Южная Корея', 'США', 'Франция', 'Германия', 'Беларусь']
COUNTRY_FLAGS = {
    'Россия': '🇷🇺', 'Китай': '🇨🇳', 'Япония': '🇯🇵', 'Южная Корея': '🇰🇷',
    'США': '🇺🇸', 'Франция': '🇫🇷', 'Германия': '🇩🇪', 'Беларусь': '🇧🇾',
}

df_brands = df_base[df_base['Tag_clean'].isin(BRAND_TAGS)].copy()
df_brands['Country'] = df_brands['Tag_clean'].map(BRAND_COUNTRY)
df_brands = df_brands.dropna(subset=['Country'])

brands_monthly = df_brands.groupby('Date')['Count'].sum().reset_index()
brands_avg_month = float(brands_monthly.tail(12)['Count'].mean()) if not brands_monthly.empty else 0.0

def get_country_ytd(country, year, max_month):
    if df_brands.empty:
        return 0.0
    mask = (df_brands['Country'] == country) & (df_brands['Date'].dt.year == year)
    if year == curr_year:
        mask &= df_brands['Date'].dt.month <= max_month
    return float(df_brands[mask]['Count'].sum())

country_ytd = {c: get_country_ytd(c, curr_year, curr_month) for c in COUNTRY_ORDER}
country_yoy = {
    c: calculate_yoy(get_country_ytd(c, curr_year, curr_month), get_country_ytd(c, curr_year - 1, curr_month))
    for c in COUNTRY_ORDER
}

brands_volume_leader = max(country_ytd, key=country_ytd.get) if any(country_ytd.values()) else '-'
brands_growth_leader = max(country_yoy, key=country_yoy.get) if country_yoy else '-'
brands_decline_leader = min(country_yoy, key=country_yoy.get) if country_yoy else '-'

share_years = [y for y in [2023, 2024, 2025, curr_year] if y <= curr_year]
country_shares = {}
for year in share_years:
    if year == curr_year:
        year_df = df_brands[(df_brands['Date'].dt.year == year) & (df_brands['Date'].dt.month <= curr_month)]
    else:
        year_df = df_brands[df_brands['Date'].dt.year == year]
    year_sum = year_df.groupby('Country')['Count'].sum()
    total = float(year_sum.sum())
    shares = {}
    for country in COUNTRY_ORDER:
        shares[country] = int(round((float(year_sum.get(country, 0)) / total) * 100)) if total > 0 else 0
    if total > 0:
        diff = 100 - sum(shares.values())
        if diff != 0:
            max_country = max(shares, key=shares.get)
            shares[max_country] += diff
    country_shares[str(year)] = shares

country_trends = {}
for country in COUNTRY_ORDER:
    df_country = df_brands[df_brands['Country'] == country].groupby('Date')['Count'].sum().reset_index()
    country_trends[country] = get_values(df_country, dates_ref)

brand_ytd_totals = (
    df_brands[(df_brands['Date'].dt.year == curr_year) & (df_brands['Date'].dt.month <= curr_month)]
    .groupby('Tag_clean')['Count']
    .sum()
    .sort_values(ascending=False)
)
top10_tags = brand_ytd_totals.head(10).index.tolist()
top1_brand = BRAND_DISPLAY.get(top10_tags[0], top10_tags[0]) if top10_tags else '-'
top10_brands = [BRAND_DISPLAY.get(t, t) for t in top10_tags]
top10_series = {}
for tag in top10_tags:
    df_brand = df_brands[df_brands['Tag_clean'] == tag].groupby('Date')['Count'].sum().reset_index()
    top10_series[BRAND_DISPLAY.get(tag, tag)] = get_values(df_brand, dates_ref)

brand_yoy_map = {}
for tag in top10_tags:
    curr = float(
        df_brands[(df_brands['Tag_clean'] == tag) & (df_brands['Date'].dt.year == curr_year) & (df_brands['Date'].dt.month <= curr_month)]['Count'].sum()
    )
    prev = float(
        df_brands[(df_brands['Tag_clean'] == tag) & (df_brands['Date'].dt.year == curr_year - 1) & (df_brands['Date'].dt.month <= curr_month)]['Count'].sum()
    )
    brand_yoy_map[BRAND_DISPLAY.get(tag, tag)] = calculate_yoy(curr, prev)

fastest_growing_brand = max(brand_yoy_map, key=brand_yoy_map.get) if brand_yoy_map else '-'
fastest_growing_brand_yoy = brand_yoy_map.get(fastest_growing_brand, 0.0)

# --- 8. ГЕНЕРАЦИЯ ИИ-СВОДОК ---
months_ru = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
period_str = f"январь-{months_ru[curr_month]} {curr_year} года"

# --- Считаем балл для ИИ точно так же, как на фронтенде (динамически) ---
max_index_limit = final_df['Index'].max() * 1.1
current_index_val = final_df['Index'].iloc[-1]
ai_score = 0.0
if max_index_limit > 0:
    ai_score = round((current_index_val / max_index_limit) * 10, 1)

print("⏳ Генерируем ИИ-сводку для Индустрии...")
prompt_ind = (
    f"Анализ авторынка РФ за {period_str}. Интерес: {yoy_growth}%. Продажи: {sales_yoy_growth}%. "
    f"Спрос на классифайды: {agg_yoy_growth}%. Индекс активности: {ai_score} из 10.\n"
    "Напиши краткую аналитическую сводку. Обязательно структурируй текст:\n"
    "1. Сделай первый абзац с общим выводом о тренде рынка.\n"
    "2. Затем сделай маркированный список (используй дефисы) из 1-2 пунктов, где назови вероятные внешние факторы, повлиявшие на тренд (например: ставка ЦБ, автокредитование, утильсбор и т.д.)."
)
ai_text_ind = get_ai_insight(prompt_ind, YANDEX_API_KEY, FOLDER_ID)

print("⏳ Генерируем ИИ-сводку для Агрегаторов...")
prompt_agg = (
    f"Анализ агрегаторов авто объявлений за {period_str}. Интерес: {agg_yoy_growth}%. "
    f"Доля агрегаторов от всех запросов: {agg_share}%. Лидер: {agg_leader}. "
    f"Доли ТОП-3: Авито ({avito_share}%), Авто.ру ({autoru_share}%), Дром ({drom_share}%).\n"
    "Напиши краткую аналитическую сводку. Строго соблюдай структуру:\n"
    "1. Вводный абзац с оценкой общего тренда агрегаторов.\n"
    "2. Маркированный список (с дефисами), где кратко объясни позиции каждого из ТОП-3 (конкуренция, маркетинг, региональная специфика). Не выделяй названия брендов никакими знаками (например **)"
)
ai_text_agg = get_ai_insight(prompt_agg, YANDEX_API_KEY, FOLDER_ID)

print("⏳ Генерируем ИИ-сводку для Поддержанных авто...")
prompt_used = (
    f"Анализ рынка поддержанных автомобилей за {period_str}. "
    f"YTD интерес: {used_yoy_growth}%. Доля запросов от всей категории: {used_share}%. "
    f"YTD продаж поддержанных авто: {used_sales_yoy}%. "
    f"Доля продаж поддержанных в паре новые/поддержанные: {avg_used_sales_share}%. "
    f"Индекс покупательской активности: {used_ai_score} из 10.\n"
    "Напиши краткую аналитическую сводку. Обязательно структурируй текст:\n"
    "1. Сделай первый абзац с общим выводом о тренде рынка поддержанных автомобилей.\n"
    "2. Затем сделай маркированный список (используй дефисы) из 1-2 пунктов, где назови вероятные внешние факторы, "
    "повлиявшие на тренд (например: ставка ЦБ, автокредитование, параллельный импорт, утильсбор и т.д.)."
)
ai_text_used = get_ai_insight(prompt_used, YANDEX_API_KEY, FOLDER_ID)

print("⏳ Генерируем ИИ-сводку для Новых авто...")
prompt_new = (
    f"Анализ рынка новых автомобилей за {period_str}. "
    f"YTD интерес: {new_yoy_growth}%. Доля запросов среди новых и поддержанных авто: {new_share}%. "
    f"YTD продаж новых авто: {new_sales_yoy}%. "
    f"Доля продаж новых авто в паре новые/поддержанные: {avg_new_sales_share}%. "
    f"Индекс покупательской активности: {new_ai_score} из 10.\n"
    "Напиши краткую аналитическую сводку. Обязательно структурируй текст:\n"
    "1. Сделай первый абзац с общим выводом о тренде рынка новых автомобилей.\n"
    "2. Затем сделай маркированный список (используй дефисы) из 1-2 пунктов, где назови вероятные внешние факторы, "
    "повлиявшие на тренд (например: ставка ЦБ, автокредитование, параллельный импорт, утильсбор, доступность новых моделей и т.д.)."
)
ai_text_new = get_ai_insight(prompt_new, YANDEX_API_KEY, FOLDER_ID)

print("⏳ Генерируем ИИ-сводку для Марок авто...")
country_changes = ', '.join([f"{c}: {country_yoy[c]}%" for c in COUNTRY_ORDER if country_yoy.get(c) is not None])
top3_countries = sorted(country_ytd.items(), key=lambda x: x[1], reverse=True)[:3]
top3_countries_str = ', '.join([f"{c} ({v/1e6:.1f} млн)" for c, v in top3_countries])
prompt_brands = (
    f"Анализ интереса к маркам автомобилей за {period_str}. "
    f"Средний месячный интерес по маркам: {round(brands_avg_month/1e6, 1)} млн запросов. "
    f"Лидер по объему YTD {curr_year}: {brands_volume_leader}. "
    f"Лидер по росту YTD: {brands_growth_leader} ({country_yoy.get(brands_growth_leader, 0)}%). "
    f"Лидер по снижению YTD: {brands_decline_leader} ({country_yoy.get(brands_decline_leader, 0)}%). "
    f"ТОП-3 страны по объему: {top3_countries_str}. "
    f"Динамика YTD по странам: {country_changes}. "
    f"Самый быстрорастущий бренд в ТОП-10: {fastest_growing_brand} ({fastest_growing_brand_yoy}%).\n"
    "Напиши краткую аналитическую сводку. Обязательно структурируй текст:\n"
    "1. Первый абзац — общий вывод о тренде интереса к маркам авто по странам.\n"
    "2. Маркированный список (дефисы) из 2-3 пунктов: какие страны заметно выросли/упали; "
    "какой бренд растет быстрее всего и возможные причины (модельный ряд, цена, локализация)."
)
ai_text_brands = get_ai_insight(prompt_brands, YANDEX_API_KEY, FOLDER_ID)

# --- 9. ФОРМИРОВАНИЕ JSON ---
output = {
    "updated_at": datetime.now().strftime("%Y-%m-%d"),
    "kpi": {
        "avg_monthly_interest": round(avg_interest_year / 1000000, 1),
        "yoy_interest_growth": yoy_growth,
        "sales_yoy_growth": sales_yoy_growth,
        "aggregator_yoy": agg_yoy_growth,
        "current_index": float(final_df['Index'].iloc[-1]) if not pd.isna(final_df['Index'].iloc[-1]) else 0.0,
        "max_index": float(final_df['Index'].max()) 
    },
    "kpi_agg": {
        "avg_monthly": round(agg_avg_month / 1000000, 1),
        "share": agg_share,
        "yoy": agg_yoy_main if 'agg_yoy_main' in locals() else agg_yoy_growth,
        "leader": agg_leader
    },
    "charts": {
        "main_trend": {
            "dates": final_df['Date'].dt.strftime('%Y-%m-%d').tolist(),
            "interest": final_df['Count'].astype(float).tolist(),
            "sales": final_df['Sales_Count'].fillna(0).astype(float).tolist(),
            "seasonality": final_df['is_high_season'].tolist()
        },
        "activity_index": {
            "values": final_df['Index'].fillna(0).astype(float).tolist()
        }
    },
    "charts_agg": {
        "main_interest": agg_interest_list,
        "top3_avito": avito_list,
        "top3_autoru": autoru_list,
        "top3_drom": drom_list,
        "top3_shares": {
            "avito": avito_share,
            "autoru": autoru_share,
            "drom": drom_share,
            "year": curr_year
        }
    },
    "kpi_used": {
        "avg_monthly": round(used_avg_month / 1000000, 1),
        "share": used_share,
        "yoy_interest": used_yoy_growth,
        "yoy_sales": used_sales_yoy,
        "current_index": used_current_index,
        "max_index": used_max_index
    },
    "charts_used": {
        "main_trend": {
            "dates": used_final['Date'].dt.strftime('%Y-%m-%d').tolist(),
            "interest": used_final['Count'].astype(float).tolist(),
            "sales": used_final['Sales_Count'].astype(float).tolist(),
            "seasonality": used_final['is_high_season'].tolist()
        },
        "sales_mix": {
            "new_sales": new_sales_list,
            "used_sales": used_sales_list,
            "used_pct": used_sales_pct
        },
        "activity_index": {
            "values": used_final['Index'].astype(float).tolist()
        }
    },
    "kpi_new": {
        "avg_monthly": round(new_avg_month / 1000000, 1),
        "share": new_share,
        "yoy_interest": new_yoy_growth,
        "yoy_sales": new_sales_yoy,
        "current_index": new_current_index,
        "max_index": new_max_index
    },
    "charts_new": {
        "main_trend": {
            "dates": new_final['Date'].dt.strftime('%Y-%m-%d').tolist(),
            "interest": new_final['Count'].astype(float).tolist(),
            "sales": new_final['Sales_Count'].astype(float).tolist(),
            "seasonality": new_final['is_high_season'].tolist()
        },
        "sales_mix": {
            "new_sales": new_sales_list,
            "used_sales": used_sales_list,
            "new_pct": new_sales_pct
        },
        "activity_index": {
            "values": new_final['Index'].astype(float).tolist()
        }
    },
    "ai_insight": ai_text_ind,
    "ai_insight_agg": ai_text_agg,
    "ai_insight_used": ai_text_used,
    "ai_insight_new": ai_text_new,
    "kpi_brands": {
        "top1_brand": top1_brand,
        "volume_leader": brands_volume_leader,
        "growth_leader": brands_growth_leader,
        "decline_leader": brands_decline_leader,
        "volume_leader_flag": COUNTRY_FLAGS.get(brands_volume_leader, ''),
        "growth_leader_flag": COUNTRY_FLAGS.get(brands_growth_leader, ''),
        "decline_leader_flag": COUNTRY_FLAGS.get(brands_decline_leader, ''),
    },
    "charts_brands": {
        "dates": [d.strftime('%Y-%m-%d') for d in dates_ref],
        "country_order": COUNTRY_ORDER,
        "country_colors": {
            'Россия': '#a855f7', 'Китай': '#ec4899', 'Япония': '#38bdf8',
            'Южная Корея': '#22d3ee', 'США': '#cbd5e1', 'Франция': '#14b8a6',
            'Германия': '#64748b', 'Беларусь': '#c084fc',
        },
        "country_shares": country_shares,
        "country_trends": country_trends,
        "top10": {
            "brands": top10_brands,
            "series": top10_series,
        },
    },
    "ai_insight_brands": ai_text_brands
}

for json_path in ('data.json', '../frontend/data.json'):
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

print("✅ JSON успешно обновлен! (Индустрия, агрегаторы, поддержанные авто, новые авто, марки авто)")