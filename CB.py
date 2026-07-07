from __future__ import annotations

import contextlib
import html
import hashlib
import io
import logging
import os
import re
import warnings
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
import streamlit as st
import yfinance as yf


logging.getLogger("yfinance").setLevel(logging.CRITICAL)

try:
    from urllib3.exceptions import InsecureRequestWarning

    warnings.simplefilter("ignore", InsecureRequestWarning)
except Exception:
    pass


st.set_page_config(
    page_title="台灣可轉債 CB 戰術監控",
    page_icon="📈",
    layout="wide",
)


TPEX_OPENAPI_URL = "https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data"
TWSE_DAILY_QUOTES_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
KEYWORDS = ("公告", "轉換公司債", "轉換價格")
REQUIRED_COLUMNS = ["CB代碼", "CB名稱", "股票代號", "轉換價", "標記"]
DISPLAY_COLUMNS = [
    "標記",
    "策略訊號",
    "分類",
    "CB代碼",
    "CB名稱",
    "股票代號",
    "報價來源",
    "報價代碼",
    "轉換價",
    "現股價格",
    "轉換價值",
    "溢折價率",
    "市值(億)",
    "事件溢價率",
    "掛牌前20交易日漲幅",
    "20日動能錨點",
    "5日量比",
    "掛牌日量比",
    "Day起算日",
    "事件日來源",
    "定價公告日",
    "預計掛牌日",
    "目前Day",
    "目前Day顯示",
    "買進時機",
    "掛牌前後階段",
    "回測樣本數",
    "回測勝率",
    "回測中位數",
    "回測平均報酬",
    "回測平均最大不利",
    "策略錨定日",
    "策略備註",
    "上市日期",
    "到期日期",
    "資料來源",
    "重訊標題",
    "重訊連結",
    "更新時間",
]
NUMERIC_COLUMNS = {
    "轉換價",
    "現股價格",
    "轉換價值",
    "溢折價率",
    "市值(億)",
    "事件溢價率",
    "掛牌前20交易日漲幅",
    "5日量比",
    "掛牌日量比",
    "目前Day",
    "回測樣本數",
    "回測勝率",
    "回測中位數",
    "回測平均報酬",
    "回測平均最大不利",
    "排序權重",
    "分類權重",
    "策略權重",
}
STRATEGY_SIGNAL_STRONG = "🔥 強勢續拉型"
STRATEGY_SIGNAL_CANDIDATE = "🟡 強勢續拉候選"
STRATEGY_SIGNAL_OVERHEAT = "⚠️ 過熱出貨疑慮"
STRATEGY_SIGNAL_PRESSURE = "🟦 壓價觀察型"
STRATEGY_SIGNAL_NORMAL = "—"
PRELISTING_BACKTEST_STATS = {
    -20: {"events": 371, "avg": 7.52, "median": 3.37, "win": 62.80},
    -15: {"events": 371, "avg": 5.76, "median": 2.33, "win": 60.11},
    -10: {"events": 371, "avg": 3.02, "median": 0.79, "win": 54.18},
    -7: {"events": 371, "avg": 1.54, "median": 0.44, "win": 52.02},
    -5: {"events": 371, "avg": 0.63, "median": 0.00, "win": 46.90},
    -3: {"events": 371, "avg": 0.14, "median": -0.35, "win": 43.40},
    -1: {"events": 371, "avg": -0.00, "median": -0.21, "win": 42.05},
}
STRONG_PRELISTING_BACKTEST_STATS = {
    -20: {"events": 12, "avg": 45.14, "median": 42.05, "win": 100.00},
    -15: {"events": 12, "avg": 38.62, "median": 38.25, "win": 100.00},
    -10: {"events": 12, "avg": 18.87, "median": 17.88, "win": 100.00},
    -7: {"events": 12, "avg": 10.92, "median": 9.66, "win": 83.33},
    -5: {"events": 12, "avg": 9.02, "median": 10.78, "win": 83.33},
    -3: {"events": 12, "avg": 4.62, "median": 6.09, "win": 66.67},
    -1: {"events": 12, "avg": 2.25, "median": -0.18, "win": 50.00},
}
ENTRY_DAY_BACKTEST_STATS = {
    0: {"events": 12, "avg": 20.43, "median": 11.51, "win": 83.33, "mae": -7.09, "dd5": 41.67, "dd10": 25.00},
    1: {"events": 12, "avg": 18.77, "median": 8.95, "win": 83.33, "mae": -6.60, "dd5": 41.67, "dd10": 33.33},
    2: {"events": 12, "avg": 15.69, "median": 9.51, "win": 75.00, "mae": -8.70, "dd5": 58.33, "dd10": 33.33},
    3: {"events": 12, "avg": 15.76, "median": 8.18, "win": 58.33, "mae": -8.74, "dd5": 66.67, "dd10": 25.00},
    4: {"events": 12, "avg": 15.01, "median": 10.93, "win": 75.00, "mae": -9.19, "dd5": 50.00, "dd10": 41.67},
    5: {"events": 12, "avg": 15.81, "median": 9.96, "win": 75.00, "mae": -8.72, "dd5": 33.33, "dd10": 33.33},
    6: {"events": 12, "avg": 12.78, "median": 8.45, "win": 75.00, "mae": -9.68, "dd5": 66.67, "dd10": 41.67},
    7: {"events": 12, "avg": 9.80, "median": 6.33, "win": 66.67, "mae": -10.74, "dd5": 75.00, "dd10": 50.00},
    8: {"events": 12, "avg": 8.91, "median": 10.29, "win": 66.67, "mae": -10.98, "dd5": 66.67, "dd10": 50.00},
    9: {"events": 12, "avg": 12.13, "median": 15.74, "win": 66.67, "mae": -8.35, "dd5": 58.33, "dd10": 33.33},
    10: {"events": 12, "avg": 11.79, "median": 16.22, "win": 66.67, "mae": -7.71, "dd5": 41.67, "dd10": 33.33},
    11: {"events": 12, "avg": 12.04, "median": 18.84, "win": 66.67, "mae": -6.49, "dd5": 50.00, "dd10": 25.00},
    12: {"events": 12, "avg": 9.75, "median": 10.26, "win": 58.33, "mae": -6.01, "dd5": 50.00, "dd10": 25.00},
    13: {"events": 12, "avg": 7.77, "median": 6.52, "win": 66.67, "mae": -5.81, "dd5": 50.00, "dd10": 25.00},
    14: {"events": 12, "avg": 6.53, "median": 7.36, "win": 66.67, "mae": -6.14, "dd5": 41.67, "dd10": 41.67},
    15: {"events": 12, "avg": 5.88, "median": 6.57, "win": 66.67, "mae": -5.27, "dd5": 41.67, "dd10": 33.33},
    16: {"events": 12, "avg": 3.21, "median": 1.77, "win": 50.00, "mae": -5.39, "dd5": 41.67, "dd10": 25.00},
    17: {"events": 12, "avg": 3.45, "median": 2.67, "win": 58.33, "mae": -3.91, "dd5": 41.67, "dd10": 0.00},
    18: {"events": 12, "avg": 3.72, "median": 4.35, "win": 75.00, "mae": -2.10, "dd5": 16.67, "dd10": 0.00},
    19: {"events": 12, "avg": 2.91, "median": 2.74, "win": 83.33, "mae": -0.67, "dd5": 0.00, "dd10": 0.00},
}
TMINUS20_EXIT_RULE_STATS = [
    {"規則": "固定 T-10 出場", "樣本數": 371, "平均報酬": 4.24, "中位數": 2.27, "勝率": 64.42, "平均最大不利": -3.51, "回撤<=-5%機率": 28.84, "回撤<=-10%機率": 7.55, "平均出場Day": -10.00},
    {"規則": "固定 T-7 出場", "樣本數": 371, "平均報酬": 5.82, "中位數": 2.81, "勝率": 62.80, "平均最大不利": -3.98, "回撤<=-5%機率": 31.81, "回撤<=-10%機率": 9.97, "平均出場Day": -7.00},
    {"規則": "固定 T-5 出場", "樣本數": 371, "平均報酬": 6.78, "中位數": 4.05, "勝率": 64.96, "平均最大不利": -4.20, "回撤<=-5%機率": 34.77, "回撤<=-10%機率": 11.05, "平均出場Day": -5.00},
    {"規則": "固定 T-3 出場", "樣本數": 371, "平均報酬": 7.36, "中位數": 3.70, "勝率": 64.15, "平均最大不利": -4.37, "回撤<=-5%機率": 36.66, "回撤<=-10%機率": 12.94, "平均出場Day": -3.00},
    {"規則": "固定 T-1 出場", "樣本數": 371, "平均報酬": 7.49, "中位數": 3.33, "勝率": 64.69, "平均最大不利": -4.64, "回撤<=-5%機率": 38.01, "回撤<=-10%機率": 15.36, "平均出場Day": -1.00},
    {"規則": "TP+10 / SL-5 / 強制T-5", "樣本數": 371, "平均報酬": 2.96, "中位數": 2.44, "勝率": 56.60, "平均最大不利": -3.23, "回撤<=-5%機率": 33.69, "回撤<=-10%機率": 2.70, "平均出場Day": -10.73},
    {"規則": "TP+12 / SL-6 / 強制T-3", "樣本數": 371, "平均報酬": 3.80, "中位數": 3.06, "勝率": 58.49, "平均最大不利": -3.55, "回撤<=-5%機率": 35.31, "回撤<=-10%機率": 3.23, "平均出場Day": -8.41},
    {"規則": "TP+15 / SL-8 / 強制T-3", "樣本數": 371, "平均報酬": 4.16, "中位數": 3.17, "勝率": 59.84, "平均最大不利": -3.94, "回撤<=-5%機率": 36.66, "回撤<=-10%機率": 6.74, "平均出場Day": -6.79},
    {"規則": "T-10未漲3%先出；否則TP+12/SL-6/T-3", "樣本數": 371, "平均報酬": 3.30, "中位數": 1.43, "勝率": 57.95, "平均最大不利": -3.14, "回撤<=-5%機率": 29.11, "回撤<=-10%機率": 2.70, "平均出場Day": -10.66},
    {"規則": "漲8%後回吐4%出；SL-6；強制T-3", "樣本數": 371, "平均報酬": 4.55, "中位數": 3.20, "勝率": 60.92, "平均最大不利": -3.48, "回撤<=-5%機率": 34.77, "回撤<=-10%機率": 3.23, "平均出場Day": -7.78},
]

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/xml, application/xml, */*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.tpex.org.tw/",
}

FIELD_CANDIDATES = {
    "CB代碼": [
        "CB代碼",
        "可轉債代號",
        "可轉換公司債代號",
        "債券代號",
        "證券代號",
        "有價證券代號",
        "SecCode",
        "BondCode",
        "bond_code",
    ],
    "CB名稱": [
        "CB名稱",
        "可轉債名稱",
        "可轉換公司債名稱",
        "債券名稱",
        "證券名稱",
        "有價證券名稱",
        "ShortName",
        "IssuerName",
        "SecName",
        "BondName",
        "bond_name",
    ],
    "股票代號": [
        "股票代號",
        "發行公司代號",
        "標的股票代號",
        "轉換標的股票代號",
        "標的代號",
        "正股代號",
        "IssuerCode",
        "UnderlyingStockCode",
        "UndlCode",
        "StockCode",
        "stock_code",
    ],
    "轉換價": [
        "轉換價",
        "轉換價格",
        "最新轉換價格",
        "最新轉(交)換價格",
        "轉(交)換價格",
        "ConversionPrice",
        "ConvertPrice",
        "ConvPrice",
        "ExPrice",
        "Conversion/ExchangePriceAtIssuance",
    ],
}


def default_feed_urls() -> tuple[str, ...]:
    news_queries = (
        "公告 轉換公司債 轉換價格",
        "轉換公司債 轉換價格 溢價率",
        "轉換債 轉換價格 訂每股",
        "轉換公司債 預計掛牌日 上櫃買賣",
    )
    google_feeds = tuple(
        f"https://news.google.com/rss/search?q={quote_plus(query)}+when:60d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        for query in news_queries
    )
    return (
        "https://tw.stock.yahoo.com/rss?category=news",
        "https://tw.stock.yahoo.com/rss?category=tw-market",
        *google_feeds,
    )


def empty_cb_frame() -> pd.DataFrame:
    columns = list(dict.fromkeys(DISPLAY_COLUMNS + ["排序權重", "分類權重", "策略權重"]))
    return pd.DataFrame(columns=columns)


def ensure_columns(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        df = empty_cb_frame()
    elif not df.columns.is_unique:
        df = df.loc[:, ~df.columns.duplicated()].copy()

    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col != "轉換價" else 0.0

    for col in dict.fromkeys(DISPLAY_COLUMNS + ["排序權重", "分類權重", "策略權重"]):
        if col not in df.columns:
            df[col] = 0.0 if col in NUMERIC_COLUMNS else ""

    return df


def normalize_key(value: Any) -> str:
    return re.sub(r"[\s_()（）:：/\\-]+", "", str(value)).lower()


def clean_number(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value)
    text = (
        text.replace(",", "")
        .replace("，", "")
        .replace("元", "")
        .replace("新臺幣", "")
        .replace("新台幣", "")
        .strip()
    )
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def format_taiwan_date(value: Any, empty_value: str = "") -> str:
    text = re.sub(r"\D", "", str(value or ""))
    if not text or text == "0":
        return empty_value

    if len(text) == 8:
        year = int(text[:4])
        month = int(text[4:6])
        day = int(text[6:8])
    elif len(text) == 7:
        year = int(text[:3]) + 1911
        month = int(text[3:5])
        day = int(text[5:7])
    else:
        return str(value or empty_value).strip()

    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return str(value or empty_value).strip()


def first_non_empty(item: dict[str, Any], candidates: list[str]) -> Any:
    normalized_item = {normalize_key(key): value for key, value in item.items()}
    for candidate in candidates:
        raw_value = item.get(candidate)
        if raw_value not in (None, ""):
            return raw_value

        raw_value = normalized_item.get(normalize_key(candidate))
        if raw_value not in (None, ""):
            return raw_value
    return ""


def fallback_conversion_price(item: dict[str, Any]) -> float:
    for key, value in item.items():
        key_text = str(key)
        normalized_key = normalize_key(key_text)
        is_conversion_price_key = (
            ("轉" in key_text and "換" in key_text and ("價" in key_text or "格" in key_text))
            or "conversionexchangeprice" in normalized_key
            or "conversionprice" in normalized_key
            or "exchangeprice" in normalized_key
        )
        if is_conversion_price_key:
            price = clean_number(value)
            if price > 0:
                return price
    return 0.0


def normalize_stock_code(value: Any) -> str:
    text = str(value).strip()
    if re.fullmatch(r"\d{4,8}", text):
        text = text[-4:]
    match = re.search(r"\b(\d{4})\b", text)
    return match.group(1) if match else ""


def normalize_cb_code(value: Any) -> str:
    match = re.search(r"\b(\d{5,6})\b", str(value))
    return match.group(1) if match else ""


def collect_stock_codes(payload: Any) -> set[str]:
    codes: set[str] = set()
    code_key_pattern = re.compile(r"(code|代號|證券|公司)", re.IGNORECASE)

    if isinstance(payload, dict):
        for key, value in payload.items():
            if code_key_pattern.search(str(key)):
                code = normalize_stock_code(value)
                if code:
                    codes.add(code)
            if isinstance(value, (dict, list, tuple)):
                codes.update(collect_stock_codes(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, (list, tuple)) and item:
                code = normalize_stock_code(item[0])
                if code:
                    codes.add(code)
            elif isinstance(item, dict):
                codes.update(collect_stock_codes(item))

    return codes


def collect_stock_catalog(payload: Any) -> dict[str, str]:
    catalog: dict[str, str] = {}
    code_candidates = ["Code", "SecuritiesCompanyCode", "公司代號", "證券代號", "股票代號"]
    name_candidates = ["Name", "CompanyName", "公司名稱", "證券名稱", "股票名稱"]

    if isinstance(payload, dict):
        raw_code = str(first_non_empty(payload, code_candidates) or "").strip()
        code = raw_code if re.fullmatch(r"\d{4}", raw_code) else ""
        name = str(first_non_empty(payload, name_candidates) or "").strip()
        if code and name:
            catalog[code] = name

        for value in payload.values():
            if isinstance(value, (dict, list, tuple)):
                catalog.update(collect_stock_catalog(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, (dict, list, tuple)):
                catalog.update(collect_stock_catalog(item))

    return catalog


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_taiwan_stock_catalog() -> dict[str, str]:
    catalog: dict[str, str] = {}

    for url in (TWSE_DAILY_QUOTES_URL, TPEX_DAILY_QUOTES_URL):
        try:
            response = requests.get(url, headers=HTTP_HEADERS, timeout=20, verify=False)
            response.raise_for_status()
            catalog.update(collect_stock_catalog(response.json()))
        except Exception:
            continue

    return catalog


def strip_html(text: Any) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_keyword_hit(text: str) -> bool:
    compact = compact_text(text)
    has_bond_term = any(term in compact for term in ("轉換公司債", "轉換債", "可轉債"))
    has_price_term = "轉換價格" in compact or "轉換價" in compact
    return has_bond_term and has_price_term


def is_potential_cb_pricing_text(text: str) -> bool:
    compact = compact_text(text)
    return ("轉換" in compact and "債" in compact and ("價格" in compact or "溢價率" in compact))


def is_new_pricing_case(text: str) -> bool:
    compact = compact_text(text)
    if re.search(r"(達公布注意交易資訊標準|注意交易資訊|最新轉.{0,3}換價格)", compact):
        return False

    has_new_pricing_hint = bool(
        re.search(
            r"(轉換價格及溢價率|訂定轉換價格|轉換價格.{0,8}(訂|為|每股)|"
            r"轉換債.{0,30}轉換價格|轉換公司債.{0,30}轉換價格|"
            r"發行.+轉換公司債.+轉換價格)",
            compact,
        )
    )
    return is_keyword_hit(compact) and has_new_pricing_hint


def extract_stock_identity(text: str) -> tuple[str, str]:
    def clean_company_name(raw_name: str) -> str:
        name = raw_name.strip("-：:，,。 ")
        for separator in ("公告", "本公司", "公司"):
            if separator in name and not name.endswith(separator):
                name = name.split(separator, 1)[0]
        return name.strip("-：:，,。 ")[:12]

    patterns = [
        r"[（(](?P<code>\d{4})[)）]\s*(?P<name>[^\s\-：:，,。]{1,16})",
        r"(?P<name>[\u4e00-\u9fffA-Za-z0-9\-]{2,16})\s*[（(](?P<code>\d{4})[)）]",
        r"(?P<name>[\u4e00-\u9fffA-Za-z0-9\-]{2,16})\s+(?P<code>\d{4})",
        r"(?P<code>\d{4})\s*(?P<name>[^\s\-：:，,。]{1,16})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            code = match.group("code")
            name = clean_company_name(match.groupdict().get("name", ""))
            return code, name

    try:
        catalog = fetch_taiwan_stock_catalog()
    except Exception:
        catalog = {}

    for code, name in sorted(catalog.items(), key=lambda item: len(item[1]), reverse=True):
        if len(name) >= 2 and name in text:
            return code, name

    return "", ""


def extract_conversion_price(text: str) -> float:
    number_pattern = r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    patterns = [
        rf"訂定轉換價格(?:訂為|訂定為|為|:|：)?(?:每股)?(?:新臺幣|新台幣)?\s*{number_pattern}",
        rf"轉換價格(?:訂為|訂定為|訂|為|:|：)?(?:每股)?(?:新臺幣|新台幣)?\s*{number_pattern}",
        rf"轉換價格.{0,12}?每股(?:新臺幣|新台幣)?\s*{number_pattern}",
        rf"訂每股(?:新臺幣|新台幣)?\s*{number_pattern}",
        rf"轉換價(?:格)?[^\d]{{0,16}}{number_pattern}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            price = clean_number(match.group(1))
            if price > 0:
                return price
    return 0.0


@st.cache_data(ttl=600, show_spinner=False)
def fetch_article_text(url: str) -> str:
    if not url or "news.google.com" in url:
        return ""

    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=20, verify=False)
        response.raise_for_status()
    except Exception:
        return ""

    text = response.text
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.IGNORECASE)
    text = strip_html(text)
    return re.sub(r"\s+", " ", text)


def entry_text(entry: Any) -> str:
    parts = [
        strip_html(entry.get("title", "")),
        strip_html(entry.get("summary", "") or entry.get("description", "")),
    ]
    for content_item in entry.get("content", []) or []:
        if isinstance(content_item, dict):
            parts.append(strip_html(content_item.get("value", "")))
    return " ".join(part for part in parts if part)


@contextlib.contextmanager
def quiet_yfinance():
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        yield


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_taiwan_market_code_sets() -> tuple[set[str], set[str]]:
    twse_codes: set[str] = set()
    tpex_codes: set[str] = set()

    for url, target in (
        (TWSE_DAILY_QUOTES_URL, twse_codes),
        (TPEX_DAILY_QUOTES_URL, tpex_codes),
    ):
        try:
            response = requests.get(url, headers=HTTP_HEADERS, timeout=20, verify=False)
            response.raise_for_status()
            target.update(collect_stock_codes(response.json()))
        except Exception:
            continue

    return twse_codes, tpex_codes


def yfinance_suffix_candidates(stock_code: str) -> tuple[str, ...]:
    code = normalize_stock_code(stock_code)
    if not code:
        return ()

    try:
        twse_codes, tpex_codes = fetch_taiwan_market_code_sets()
    except Exception:
        twse_codes, tpex_codes = set(), set()

    if code in tpex_codes and code not in twse_codes:
        return (".TWO", ".TW")
    if code in twse_codes and code not in tpex_codes:
        return (".TW", ".TWO")
    return (".TW", ".TWO")


def token_fingerprint(token: str) -> str:
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def read_secret_finlab_token() -> str:
    try:
        token = st.secrets.get("FINLAB_API_TOKEN", "")
        if token:
            return str(token).strip()

        finlab_section = st.secrets.get("finlab", {})
        if hasattr(finlab_section, "get"):
            return str(finlab_section.get("token", "")).strip()
    except Exception:
        return ""
    return ""


def latest_value_from_wide_frame(frame: pd.DataFrame, code: str) -> float:
    code = normalize_stock_code(code)
    if not code or frame.empty:
        return 0.0

    columns = {str(column): column for column in frame.columns}
    column = columns.get(code)
    if column is None:
        return 0.0

    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return 0.0
    try:
        return float(series.iloc[-1])
    except Exception:
        return 0.0


def parse_dashboard_date(value: Any) -> pd.Timestamp | pd.NaT:
    text = str(value or "").strip()
    if not text or text in {"未上市", "待掛牌", "待掛牌代碼", "nan", "NaT"}:
        return pd.NaT
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT
    try:
        if getattr(parsed, "tzinfo", None) is not None:
            parsed = parsed.tz_convert("Asia/Taipei").tz_localize(None)
    except Exception:
        try:
            parsed = parsed.tz_localize(None)
        except Exception:
            pass
    return pd.Timestamp(parsed).normalize()


def format_dashboard_date(value: Any, empty_value: str = "") -> str:
    parsed = parse_dashboard_date(value)
    if pd.isna(parsed):
        return empty_value
    return pd.Timestamp(parsed).strftime("%Y-%m-%d")


def normalize_year(year_text: str, anchor: pd.Timestamp | pd.NaT = pd.NaT) -> int:
    year = int(year_text)
    if year < 1000:
        return year + 1911
    return year


def build_date_from_parts(
    year_text: str | None,
    month_text: str,
    day_text: str,
    anchor: pd.Timestamp | pd.NaT = pd.NaT,
) -> pd.Timestamp | pd.NaT:
    try:
        if year_text:
            year = normalize_year(year_text, anchor)
        elif pd.notna(anchor):
            year = int(pd.Timestamp(anchor).year)
        else:
            year = datetime.now().year
        month = int(month_text)
        day = int(day_text)
        parsed = pd.Timestamp(datetime(year, month, day).date())
        if not year_text and pd.notna(anchor) and parsed < pd.Timestamp(anchor).normalize() - pd.Timedelta(days=30):
            parsed = pd.Timestamp(datetime(year + 1, month, day).date())
        return parsed
    except Exception:
        return pd.NaT


def parse_chinese_or_numeric_date(text: str, anchor: pd.Timestamp | pd.NaT = pd.NaT) -> pd.Timestamp | pd.NaT:
    text = str(text or "")
    patterns = [
        r"(?P<year>\d{2,4})\s*[年/-]\s*(?P<month>\d{1,2})\s*[月/-]\s*(?P<day>\d{1,2})\s*日?",
        r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parsed = build_date_from_parts(
            match.groupdict().get("year"),
            match.group("month"),
            match.group("day"),
            anchor,
        )
        if pd.notna(parsed):
            return parsed
    return pd.NaT


def extract_expected_listing_date(text: str, announcement_date: Any = "") -> str:
    anchor = parse_dashboard_date(announcement_date)
    compact = re.sub(r"\s+", "", strip_html(text))
    if not compact:
        return ""

    date_pattern = r"(?:\d{2,4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*日?|\d{1,2}\s*月\s*\d{1,2}\s*日)"
    listing_terms = r"(?:預計掛牌日|掛牌日|上市日|上櫃日|上櫃買賣日|櫃檯買賣日|開始上櫃買賣|開始櫃檯買賣|開始買賣|上櫃買賣|櫃檯買賣)"
    patterns = [
        rf"{listing_terms}[：:為訂於將於]*(?P<date>{date_pattern})",
        rf"(?:預計|訂於|將於|於)?(?P<date>{date_pattern}).{{0,24}}?{listing_terms}",
        rf"{listing_terms}.{{0,24}}?(?P<date>{date_pattern})",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, compact):
            parsed = parse_chinese_or_numeric_date(match.group("date"), anchor)
            if pd.notna(parsed):
                return parsed.strftime("%Y-%m-%d")
    return ""


def finlab_wide_to_pandas(frame: Any) -> pd.DataFrame:
    df = pd.DataFrame(frame.copy())
    df.index = pd.to_datetime(df.index)
    df.columns = [str(column) for column in df.columns]
    return df.sort_index()


def value_at_or_before(frame: pd.DataFrame | None, code: str, target_date: pd.Timestamp) -> float:
    code = normalize_stock_code(code)
    if frame is None or frame.empty or not code or code not in frame.columns or pd.isna(target_date):
        return float("nan")

    series = pd.to_numeric(frame[code], errors="coerce").dropna()
    if series.empty:
        return float("nan")

    pos = series.index.searchsorted(target_date, side="right") - 1
    if pos < 0:
        return float("nan")
    return float(series.iloc[pos])


def average_before(series: pd.Series, pos: int, lookback: int, min_count: int) -> float:
    if pos <= 0:
        return float("nan")
    window = pd.to_numeric(series.iloc[max(0, pos - lookback) : pos], errors="coerce").dropna()
    if len(window) < min_count:
        return float("nan")
    return float(window.mean())


def is_valid_number(value: Any) -> bool:
    try:
        return pd.notna(value) and float(value) == float(value)
    except Exception:
        return False


def market_cap_to_yi_twd(value: float) -> float:
    if not is_valid_number(value):
        return float("nan")
    return float(value) / 100_000_000


def strategy_records_from_df(df: pd.DataFrame) -> tuple[tuple[int, str, str, str, str, float, str], ...]:
    records: list[tuple[int, str, str, str, str, float, str]] = []
    for idx, row in df.reset_index(drop=True).iterrows():
        stock_code = normalize_stock_code(row.get("股票代號", ""))
        if not stock_code:
            continue
        records.append(
            (
                int(idx),
                stock_code,
                first_non_empty_text(row.get("上市日期", "")),
                first_non_empty_text(row.get("預計掛牌日", "")),
                first_non_empty_text(row.get("定價公告日", ""), row.get("更新時間", "")),
                float(clean_number(row.get("轉換價", 0))),
                first_non_empty_text(row.get("標記", "")),
            )
        )
    return tuple(records)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_finlab_strategy_factors(
    records: tuple[tuple[int, str, str, str, str, float, str], ...],
    token_key: str,
) -> tuple[dict[int, dict[str, Any]], list[str]]:
    factors_by_idx: dict[int, dict[str, Any]] = {}
    issues: list[str] = []

    if not records or not token_key:
        return factors_by_idx, issues

    token = os.environ.get("FINLAB_API_TOKEN", "").strip()
    if not token:
        issues.append("回測策略標記需要 FinLab token；目前只顯示基本分類。")
        return factors_by_idx, issues

    try:
        import finlab
        from finlab import data
    except ImportError:
        issues.append("尚未安裝 finlab 套件，無法計算回測策略因子。請先執行：pip install finlab")
        return factors_by_idx, issues

    try:
        finlab.login(token)
        data.truncate_start = "2020-01-01"
        data.truncate_end = datetime.now().strftime("%Y-%m-%d")
        close = finlab_wide_to_pandas(data.get("price:收盤價"))
    except Exception as exc:
        issues.append(f"FinLab 收盤價讀取失敗，無法計算回測策略因子：{exc}")
        return factors_by_idx, issues

    volume: pd.DataFrame | None = None
    market_value: pd.DataFrame | None = None
    try:
        volume = finlab_wide_to_pandas(data.get("price:成交股數"))
    except Exception as exc:
        issues.append(f"FinLab 成交量讀取失敗，量比條件將無法完整判斷：{exc}")

    try:
        market_value = finlab_wide_to_pandas(data.get("etl:market_value"))
    except Exception as exc:
        issues.append(f"FinLab 市值讀取失敗，市值條件將無法完整判斷：{exc}")

    latest_close_date = close.index.max() if not close.empty else pd.NaT

    for row_idx, stock_code, listing_date_text, expected_listing_date_text, update_time_text, conversion_price, tag in records:
        if stock_code not in close.columns:
            continue

        close_series = pd.to_numeric(close[stock_code], errors="coerce").dropna()
        if close_series.empty:
            continue

        listing_date = parse_dashboard_date(listing_date_text)
        expected_listing_date = parse_dashboard_date(expected_listing_date_text)
        publish_date = parse_dashboard_date(update_time_text)
        if pd.notna(listing_date):
            event_date = listing_date
            event_source = "掛牌日"
        elif pd.notna(expected_listing_date):
            event_date = expected_listing_date
            event_source = "預計掛牌日"
        else:
            event_date = publish_date
            event_source = "定價公告日"
        has_completed_listing = pd.notna(listing_date) and listing_date <= latest_close_date and tag == "已掛牌"

        current_event_day = float("nan")
        event_trade_date = pd.NaT
        if pd.notna(event_date):
            if pd.notna(latest_close_date) and pd.Timestamp(event_date) > pd.Timestamp(latest_close_date):
                event_trade_date = pd.Timestamp(event_date)
                next_day = pd.Timestamp(latest_close_date).normalize() + pd.Timedelta(days=1)
                future_days = pd.bdate_range(next_day, pd.Timestamp(event_date).normalize())
                current_event_day = -float(len(future_days))
            else:
                event_pos = int(close_series.index.searchsorted(event_date, side="left"))
                if 0 <= event_pos < len(close_series):
                    event_trade_date = pd.Timestamp(close_series.index[event_pos])
                    current_event_day = float(len(close_series) - 1 - event_pos)
                elif event_pos >= len(close_series):
                    event_trade_date = pd.Timestamp(event_date)
                    current_event_day = float(len(close_series) - event_pos)

        if has_completed_listing:
            pos = int(close_series.index.searchsorted(listing_date, side="left"))
            anchor_type = "掛牌日"
        else:
            pos = len(close_series) - 1
            anchor_type = "候選觀察日"

        if pos < 0 or pos >= len(close_series):
            pos = len(close_series) - 1
            anchor_type = "候選觀察日"

        anchor_date = pd.Timestamp(close_series.index[pos])
        anchor_price = float(close_series.iloc[pos])
        event_gap = (anchor_price / conversion_price - 1) * 100 if conversion_price > 0 and anchor_price > 0 else float("nan")

        pre_20d_return = float("nan")
        if pos >= 20:
            prior_price = float(close_series.iloc[pos - 20])
            if prior_price > 0:
                pre_20d_return = (anchor_price / prior_price - 1) * 100

        pre_5d_volume_ratio = float("nan")
        event_volume_ratio = float("nan")
        if volume is not None and stock_code in volume.columns:
            volume_series = pd.to_numeric(volume[stock_code], errors="coerce").dropna()
            if not volume_series.empty:
                volume_pos = int(volume_series.index.searchsorted(anchor_date, side="right") - 1)
                if volume_pos >= 0:
                    pre_20d_avg_volume = average_before(volume_series, volume_pos, 20, 10)
                    pre_5d_avg_volume = average_before(volume_series, volume_pos, 5, 3)
                    event_volume = float(volume_series.iloc[volume_pos])
                    if is_valid_number(pre_20d_avg_volume) and pre_20d_avg_volume > 0:
                        pre_5d_volume_ratio = pre_5d_avg_volume / pre_20d_avg_volume
                        if has_completed_listing:
                            event_volume_ratio = event_volume / pre_20d_avg_volume

        market_cap_yi = market_cap_to_yi_twd(value_at_or_before(market_value, stock_code, anchor_date))

        factors_by_idx[row_idx] = {
            "市值(億)": market_cap_yi,
            "事件溢價率": event_gap,
            "掛牌前20交易日漲幅": pre_20d_return,
            "20日動能錨點": anchor_type,
            "5日量比": pre_5d_volume_ratio,
            "掛牌日量比": event_volume_ratio,
            "Day起算日": event_trade_date.strftime("%Y-%m-%d") if pd.notna(event_trade_date) else "",
            "事件日來源": event_source,
            "定價公告日": publish_date.strftime("%Y-%m-%d") if pd.notna(publish_date) else "",
            "目前Day": current_event_day,
            "策略錨定日": anchor_date.strftime("%Y-%m-%d"),
            "策略錨定類型": anchor_type,
            "完整掛牌事件": has_completed_listing,
        }

    return factors_by_idx, issues


def pass_min(value: Any, threshold: float) -> bool:
    return is_valid_number(value) and float(value) >= threshold


def pass_max(value: Any, threshold: float) -> bool:
    return is_valid_number(value) and float(value) <= threshold


def pass_gt(value: Any, threshold: float) -> bool:
    return is_valid_number(value) and float(value) > threshold


def safe_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def first_non_empty_text(*values: Any) -> str:
    for value in values:
        text = safe_text(value).strip()
        if text:
            return text
    return ""


def safe_bool(value: Any) -> bool:
    if value is None or pd.isna(value):
        return False
    return bool(value)


def format_event_day(value: Any) -> str:
    if not is_valid_number(value):
        return "Day 未知"
    day = int(float(value))
    return f"T{day:+d}"


def event_day_window_note(value: Any) -> str:
    if not is_valid_number(value):
        return "尚無事件日可對齊。"
    day = int(float(value))
    if day < -10:
        return "仍在掛牌前早段，等待定價與量價結構確認。"
    if day <= -5:
        return "位於掛牌前預反應觀察窗 T-10~T-5。"
    if day < 0:
        return "已接近掛牌，歷史上追價優勢下降。"
    if day < 10:
        return "尚未進入回測主要優勢窗 T+10~T+20。"
    if day < 20:
        return "位於回測主要優勢窗 T+10~T+20。"
    if day == 20:
        return "已到固定觀察出場點 T+20，不再視為新的買進統計。"
    return "已超過回測主要優勢窗，留意續航與停利。"


def entry_timing_label(value: Any) -> str:
    if not is_valid_number(value):
        return "Day 未知"
    day = int(float(value))
    if day < -10:
        return "掛牌前早段"
    if day <= -5:
        return "預掛牌強勢窗"
    if day < 0:
        return "掛牌前不追"
    if day <= 3:
        return "早期觀察，不重倉"
    if day <= 8:
        return "洗盤確認期"
    if day <= 11:
        return "最佳切入窗"
    if day <= 19:
        return "續拉/停利窗"
    if day == 20:
        return "T+20觀察結束"
    return "不追，續航觀察"


def prelisting_stage_label(value: Any) -> str:
    return entry_timing_label(value)


def entry_backtest_stats(value: Any) -> dict[str, float]:
    if not is_valid_number(value):
        return {}
    day = int(float(value))
    if day < 0:
        nearest_day = min(PRELISTING_BACKTEST_STATS, key=lambda candidate: abs(candidate - day))
        stats = PRELISTING_BACKTEST_STATS.get(nearest_day, {})
        if not stats:
            return {}
        return {
            "events": stats["events"],
            "avg": stats["avg"],
            "median": stats["median"],
            "win": stats["win"],
            "mae": float("nan"),
        }
    if day >= 20:
        return {}
    return ENTRY_DAY_BACKTEST_STATS.get(day, {})


def classify_backtest_strategy(row: pd.Series) -> tuple[str, str, int]:
    market_ok = pass_min(row.get("市值(億)"), 80)
    momentum_ok = pass_min(row.get("掛牌前20交易日漲幅"), 20)
    gap_ok = pass_min(row.get("事件溢價率"), 2)
    pre_volume_ok = pass_max(row.get("5日量比"), 2)
    event_volume_ok = pass_max(row.get("掛牌日量比"), 1)
    completed_listing = safe_bool(row.get("完整掛牌事件", False))
    anchor_type = first_non_empty_text(row.get("20日動能錨點"), row.get("策略錨定類型"))
    momentum_label = "掛牌前20交易日漲幅>=20%" if completed_listing else "近20交易日動能>=20%"
    day_text = format_event_day(row.get("目前Day"))
    day_note = event_day_window_note(row.get("目前Day"))

    base_checks = [
        ("市值>=80億", market_ok),
        (momentum_label, momentum_ok),
        ("事件溢價>=2%", gap_ok),
        ("5日量比<=2", pre_volume_ok),
    ]
    failed = [label for label, ok in base_checks if not ok]

    if market_ok and momentum_ok and gap_ok and pre_volume_ok and completed_listing and event_volume_ok:
        return STRATEGY_SIGNAL_STRONG, f"完整命中回測條件；目前 {day_text}，{day_note}", 0

    if market_ok and momentum_ok and gap_ok and pre_volume_ok and not completed_listing:
        anchor_note = "目前用最新交易日作為候選觀察錨點" if anchor_type == "候選觀察日" else "目前用預計掛牌日/事件日附近作為候選錨點"
        return STRATEGY_SIGNAL_CANDIDATE, f"新案只屬預警，不等於完整回測命中；{anchor_note}，目前 {day_text}，等待預計掛牌日與掛牌日量比確認。", 1

    if momentum_ok and gap_ok and (pass_gt(row.get("5日量比"), 2) or pass_gt(row.get("掛牌日量比"), 1)):
        return STRATEGY_SIGNAL_OVERHEAT, "動能與價差符合，但量能偏熱，需防追高或事件日出貨。", 2

    if pass_min(row.get("事件溢價率"), -12) and pass_max(row.get("事件溢價率"), 2):
        return STRATEGY_SIGNAL_PRESSURE, "事件價差接近或低於轉換價，偏向壓價/轉換價觀察。", 3

    if failed:
        return STRATEGY_SIGNAL_NORMAL, "未通過：" + "、".join(failed[:3]), 4
    return STRATEGY_SIGNAL_NORMAL, "未形成強勢續拉型態。", 4


def apply_backtest_strategy(
    df: pd.DataFrame,
    finlab_token: str = "",
    enabled: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    df = ensure_columns(df).copy().reset_index(drop=True)
    issues: list[str] = []

    df["策略訊號"] = STRATEGY_SIGNAL_NORMAL
    df["策略備註"] = ""
    df["策略權重"] = 4
    df["完整掛牌事件"] = False
    df["買進時機"] = ""
    df["掛牌前後階段"] = ""
    df["目前Day顯示"] = ""
    for factor_col in [
        "市值(億)",
        "事件溢價率",
        "掛牌前20交易日漲幅",
        "20日動能錨點",
        "5日量比",
        "掛牌日量比",
        "目前Day",
        "回測樣本數",
        "回測勝率",
        "回測中位數",
        "回測平均報酬",
        "回測平均最大不利",
    ]:
        df[factor_col] = pd.NA
    df["Day起算日"] = ""

    if df.empty or not enabled:
        return ensure_columns(df), issues

    if finlab_token:
        os.environ["FINLAB_API_TOKEN"] = finlab_token
    else:
        issues.append("未提供 FinLab token，已略過強勢續拉型 CB 回測策略標記。")
        return ensure_columns(df), issues

    records = strategy_records_from_df(df)
    factors, factor_issues = fetch_finlab_strategy_factors(records, token_fingerprint(finlab_token))
    issues.extend(factor_issues)

    for row_idx, factor_values in factors.items():
        for key, value in factor_values.items():
            df.at[row_idx, key] = value

    for idx, row in df.iterrows():
        stats = entry_backtest_stats(row.get("目前Day"))
        df.at[idx, "目前Day顯示"] = format_event_day(row.get("目前Day"))
        df.at[idx, "買進時機"] = entry_timing_label(row.get("目前Day"))
        df.at[idx, "掛牌前後階段"] = prelisting_stage_label(row.get("目前Day"))
        if stats:
            df.at[idx, "回測樣本數"] = stats["events"]
            df.at[idx, "回測勝率"] = stats["win"]
            df.at[idx, "回測中位數"] = stats["median"]
            df.at[idx, "回測平均報酬"] = stats["avg"]
            df.at[idx, "回測平均最大不利"] = stats["mae"]

        signal, note, weight = classify_backtest_strategy(row)
        df.at[idx, "策略訊號"] = signal
        df.at[idx, "策略備註"] = note
        df.at[idx, "策略權重"] = weight

    df = df.sort_values(
        by=["排序權重", "策略權重", "分類權重", "溢折價率", "股票代號"],
        ascending=[True, True, True, False, True],
    ).reset_index(drop=True)
    return ensure_columns(df), issues


@st.cache_data(ttl=300, show_spinner=False)
def fetch_finlab_latest_quotes(stock_codes: tuple[str, ...], token_key: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    quotes: dict[str, dict[str, Any]] = {}
    issues: list[str] = []

    if not stock_codes or not token_key:
        return quotes, issues

    token = os.environ.get("FINLAB_API_TOKEN", "").strip()
    if not token:
        issues.append("FinLab token 尚未設定，已改用 yfinance 備援。")
        return quotes, issues

    try:
        import finlab
        from finlab import data
    except ImportError:
        issues.append("尚未安裝 finlab 套件，請先執行：pip install finlab")
        return quotes, issues

    try:
        finlab.login(token)
        data.truncate_start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        data.truncate_end = datetime.now().strftime("%Y-%m-%d")
        close = data.get("price:收盤價")
    except Exception as exc:
        issues.append(f"FinLab 收盤價讀取失敗，已改用 yfinance 備援：{exc}")
        return quotes, issues

    for code in stock_codes:
        price = latest_value_from_wide_frame(close, code)
        if price > 0:
            quotes[code] = {"現股價格": price, "報價來源": "FinLab", "報價代碼": code}

    return quotes, issues


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tpex_cb() -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []

    try:
        session = requests.Session()
        session.get("https://www.tpex.org.tw/", headers=HTTP_HEADERS, timeout=15, verify=False)
        response = session.get(TPEX_OPENAPI_URL, headers=HTTP_HEADERS, timeout=25, verify=False)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        issues.append(f"櫃買 OpenAPI 讀取失敗：{exc}")
        return ensure_columns(pd.DataFrame(rows)), issues

    if not isinstance(data, list):
        issues.append("櫃買 OpenAPI 回傳格式不是 list，已略過官方掛牌庫。")
        return ensure_columns(pd.DataFrame(rows)), issues

    for item in data:
        if not isinstance(item, dict):
            continue

        cb_code = normalize_cb_code(first_non_empty(item, FIELD_CANDIDATES["CB代碼"]))
        cb_name = str(first_non_empty(item, FIELD_CANDIDATES["CB名稱"]) or "").strip()
        stock_code = normalize_stock_code(first_non_empty(item, FIELD_CANDIDATES["股票代號"]))
        conversion_price = clean_number(first_non_empty(item, FIELD_CANDIDATES["轉換價"])) or fallback_conversion_price(item)
        listing_date = format_taiwan_date(item.get("ListingDate"), empty_value="未上市")
        maturity_date = format_taiwan_date(item.get("MaturityDate"))

        if not stock_code and cb_code:
            stock_code = cb_code[:4]

        if not cb_code and not stock_code:
            continue

        rows.append(
            {
                "CB代碼": cb_code,
                "CB名稱": cb_name or cb_code,
                "股票代號": stock_code,
                "轉換價": conversion_price,
                "上市日期": listing_date,
                "到期日期": maturity_date,
                "定價公告日": "",
                "預計掛牌日": listing_date,
                "事件日來源": "掛牌日",
                "標記": "已掛牌",
                "資料來源": "TPEx OpenAPI",
                "更新時間": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    df = ensure_columns(pd.DataFrame(rows))
    if not df.empty and "CB代碼" in df.columns:
        df = df.drop_duplicates(subset=["CB代碼", "股票代號"], keep="first")
    return ensure_columns(df), issues


@st.cache_data(ttl=600, show_spinner=False)
def fetch_new_pricing_cases(feed_urls: tuple[str, ...]) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    seen: set[tuple[str, float, str]] = set()

    for url in feed_urls:
        try:
            response = requests.get(url, headers=HTTP_HEADERS, timeout=20, verify=False)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
        except Exception as exc:
            issues.append(f"RSS 讀取失敗：{url}，{exc}")
            continue

        for entry in parsed.entries:
            title = strip_html(entry.get("title", ""))
            full_text = entry_text(entry)
            published_text = entry.get("published", "") or entry.get("updated", "")

            if not is_potential_cb_pricing_text(full_text):
                continue

            stock_code, stock_name = extract_stock_identity(full_text)
            conversion_price = extract_conversion_price(full_text)
            expected_listing_date = extract_expected_listing_date(full_text, published_text)

            if not is_new_pricing_case(full_text) or not stock_code or conversion_price <= 0 or not expected_listing_date:
                article_text = fetch_article_text(entry.get("link", ""))
                if article_text:
                    full_text = f"{full_text} {article_text}"
                    expected_listing_date = expected_listing_date or extract_expected_listing_date(full_text, published_text)

            stock_code, stock_name = extract_stock_identity(full_text)
            conversion_price = extract_conversion_price(full_text)
            expected_listing_date = expected_listing_date or extract_expected_listing_date(full_text, published_text)
            if not is_new_pricing_case(full_text) or not stock_code or conversion_price <= 0:
                continue

            key = (stock_code, conversion_price, title)
            if key in seen:
                continue
            seen.add(key)

            rows.append(
                {
                    "CB代碼": "待掛牌代碼",
                    "CB名稱": stock_name or f"{stock_code} 新定價案",
                    "股票代號": stock_code,
                    "轉換價": conversion_price,
                    "上市日期": "待掛牌",
                    "到期日期": "",
                    "定價公告日": format_dashboard_date(published_text),
                    "預計掛牌日": expected_listing_date or "待查",
                    "事件日來源": "預計掛牌日" if expected_listing_date else "定價公告日",
                    "標記": "新定價案",
                    "資料來源": "RSS 重訊",
                    "重訊標題": title,
                    "重訊連結": entry.get("link", ""),
                    "更新時間": published_text,
                }
            )

    return ensure_columns(pd.DataFrame(rows)), issues


@st.cache_data(ttl=120, show_spinner=False)
def fetch_stock_quote(stock_code: str) -> dict[str, Any]:
    code = normalize_stock_code(stock_code)
    if not code:
        return {"現股價格": 0.0, "報價來源": "", "報價代碼": ""}

    for suffix in yfinance_suffix_candidates(code):
        symbol = f"{code}{suffix}"
        try:
            with quiet_yfinance():
                ticker = yf.Ticker(symbol)
                fast_info = getattr(ticker, "fast_info", {}) or {}
                candidates = []
                for key in ("last_price", "regular_market_price", "previous_close"):
                    try:
                        candidates.append(float(fast_info.get(key) or 0))
                    except Exception:
                        candidates.append(0.0)

                price = next((value for value in candidates if value > 0), 0.0)
                if price <= 0:
                    history = ticker.history(period="5d", interval="1d", auto_adjust=False, raise_errors=False)
                    if not history.empty and "Close" in history.columns:
                        close_values = history["Close"].dropna()
                        price = float(close_values.iloc[-1]) if not close_values.empty else 0.0

            if price > 0:
                return {"現股價格": price, "報價來源": "yfinance", "報價代碼": symbol}
        except Exception:
            continue

    return {"現股價格": 0.0, "報價來源": "", "報價代碼": ""}


def classify_row(price: float, premium_rate: float) -> str:
    if price <= 0:
        return "⚪ 暫無報價"
    if premium_rate > 2:
        return "🚀 拉抬攻擊"
    if -12 <= premium_rate <= -0.5:
        return "🟦 疑似壓價"
    return "⏳ 觀察標的"


def enrich_with_quotes(df: pd.DataFrame, finlab_token: str = "", quote_mode: str = "FinLab 優先") -> tuple[pd.DataFrame, list[str]]:
    df = ensure_columns(df).copy()
    quote_issues: list[str] = []
    if df.empty:
        return ensure_columns(df), quote_issues

    df["股票代號"] = df["股票代號"].map(normalize_stock_code)
    df["轉換價"] = pd.to_numeric(df["轉換價"], errors="coerce").fillna(0.0)

    stock_codes = tuple(sorted({code for code in df["股票代號"].dropna().astype(str) if normalize_stock_code(code)}))
    quotes: dict[str, dict[str, Any]] = {}

    use_finlab = quote_mode in {"FinLab 優先", "只用 FinLab"}
    if use_finlab:
        if finlab_token:
            os.environ["FINLAB_API_TOKEN"] = finlab_token
            finlab_quotes, finlab_issues = fetch_finlab_latest_quotes(stock_codes, token_fingerprint(finlab_token))
            quotes.update(finlab_quotes)
            quote_issues.extend(finlab_issues)
        else:
            quote_issues.append("FinLab token 尚未設定；可在側欄輸入，或設定 FINLAB_API_TOKEN 環境變數。")

    missing_codes = [code for code in stock_codes if code not in quotes]
    if quote_mode != "只用 FinLab":
        quotes.update({code: fetch_stock_quote(code) for code in missing_codes})

    df["現股價格"] = df["股票代號"].map(lambda code: quotes.get(code, {}).get("現股價格", 0.0)).fillna(0.0)
    df["報價來源"] = df["股票代號"].map(lambda code: quotes.get(code, {}).get("報價來源", "")).fillna("")
    df["報價代碼"] = df["股票代號"].map(lambda code: quotes.get(code, {}).get("報價代碼", "")).fillna("")
    df["轉換價值"] = df.apply(
        lambda row: (100 / row["轉換價"]) * row["現股價格"] if row["轉換價"] > 0 and row["現股價格"] > 0 else 0.0,
        axis=1,
    )
    df["溢折價率"] = df.apply(
        lambda row: ((row["現股價格"] / row["轉換價"]) - 1) * 100 if row["轉換價"] > 0 and row["現股價格"] > 0 else 0.0,
        axis=1,
    )
    df["分類"] = df.apply(lambda row: classify_row(float(row["現股價格"]), float(row["溢折價率"])), axis=1)
    df["排序權重"] = df["標記"].map(lambda value: 0 if value == "新定價案" else 1)
    class_order = {"🚀 拉抬攻擊": 0, "🟦 疑似壓價": 1, "⏳ 觀察標的": 2, "⚪ 暫無報價": 3}
    df["分類權重"] = df["分類"].map(class_order).fillna(9)

    df = df.sort_values(
        by=["排序權重", "分類權重", "溢折價率", "股票代號"],
        ascending=[True, True, False, True],
    ).reset_index(drop=True)
    return ensure_columns(df), quote_issues


def render_metrics(df: pd.DataFrame) -> None:
    counts = df["分類"].value_counts() if "分類" in df.columns else pd.Series(dtype=int)
    strategy_counts = df["策略訊號"].value_counts() if "策略訊號" in df.columns else pd.Series(dtype=int)
    timing_counts = df["買進時機"].value_counts() if "買進時機" in df.columns else pd.Series(dtype=int)
    metric_items = [
        ("🔥 強勢策略", int(strategy_counts.get(STRATEGY_SIGNAL_STRONG, 0))),
        ("🟡 候選觀察", int(strategy_counts.get(STRATEGY_SIGNAL_CANDIDATE, 0))),
        ("🕰️ 預掛牌窗", int(timing_counts.get("預掛牌強勢窗", 0))),
        ("🎯 最佳切入窗", int(timing_counts.get("最佳切入窗", 0))),
        ("🚀 拉抬攻擊", int(counts.get("🚀 拉抬攻擊", 0))),
    ]
    cols = st.columns(len(metric_items))
    for col, (label, value) in zip(cols, metric_items):
        col.metric(label, value)


def render_warnings(issues: list[str]) -> None:
    for issue in issues:
        st.warning(issue)


def inject_custom_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
        }
        [data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.045);
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 8px;
            padding: 14px 16px;
        }
        [data-testid="stMetricLabel"] {
            color: rgba(250, 250, 250, 0.72);
        }
        .cb-status {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin: 0.25rem 0 1rem;
        }
        .cb-chip {
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 999px;
            padding: 5px 10px;
            background: rgba(255,255,255,0.045);
            font-size: 0.88rem;
            color: rgba(255,255,255,0.82);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def filter_dashboard(
    df: pd.DataFrame,
    search_text: str,
    tags: list[str],
    classes: list[str],
    strategy_signals: list[str],
    quote_sources: list[str],
) -> pd.DataFrame:
    filtered = ensure_columns(df).copy()

    if tags and "全部" not in tags:
        filtered = filtered[filtered["標記"].isin(tags)]

    if classes and "全部" not in classes:
        filtered = filtered[filtered["分類"].isin(classes)]

    if strategy_signals and "全部" not in strategy_signals:
        filtered = filtered[filtered["策略訊號"].isin(strategy_signals)]

    if quote_sources and "全部" not in quote_sources:
        filtered = filtered[filtered["報價來源"].isin(quote_sources)]

    query = search_text.strip().lower()
    if query:
        search_columns = [
            "CB代碼",
            "CB名稱",
            "股票代號",
            "標記",
            "分類",
            "策略訊號",
            "買進時機",
            "掛牌前後階段",
            "事件日來源",
            "定價公告日",
            "策略備註",
            "資料來源",
            "重訊標題",
        ]
        haystack = filtered[[col for col in search_columns if col in filtered.columns]].fillna("").astype(str)
        mask = haystack.apply(lambda col: col.str.lower().str.contains(re.escape(query), na=False)).any(axis=1)
        filtered = filtered[mask]

    return filtered.reset_index(drop=True)


def render_status_chips(df: pd.DataFrame, filtered_df: pd.DataFrame) -> None:
    total = len(df)
    visible = len(filtered_df)
    new_count = int((df["標記"] == "新定價案").sum()) if "標記" in df.columns else 0
    finlab_count = int((df["報價來源"] == "FinLab").sum()) if "報價來源" in df.columns else 0
    strong_count = int((df["策略訊號"] == STRATEGY_SIGNAL_STRONG).sum()) if "策略訊號" in df.columns else 0
    prelisting_count = int((df["買進時機"] == "預掛牌強勢窗").sum()) if "買進時機" in df.columns else 0
    best_entry_count = int((df["買進時機"] == "最佳切入窗").sum()) if "買進時機" in df.columns else 0
    st.markdown(
        f"""
        <div class="cb-status">
            <span class="cb-chip">顯示 {visible} / {total} 筆</span>
            <span class="cb-chip">新定價案 {new_count} 筆</span>
            <span class="cb-chip">強勢續拉型 {strong_count} 筆</span>
            <span class="cb-chip">預掛牌窗 {prelisting_count} 筆</span>
            <span class="cb-chip">最佳切入窗 {best_entry_count} 筆</span>
            <span class="cb-chip">FinLab 報價 {finlab_count} 筆</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def entry_day_backtest_table() -> pd.DataFrame:
    rows = []
    for day, stats in ENTRY_DAY_BACKTEST_STATS.items():
        rows.append(
            {
                "目前Day": day,
                "買進時機": entry_timing_label(day),
                "樣本數": stats["events"],
                "平均報酬": stats["avg"],
                "中位數": stats["median"],
                "勝率": stats["win"],
                "平均最大不利": stats["mae"],
                "回撤<=-5%機率": stats.get("dd5", float("nan")),
                "回撤<=-10%機率": stats.get("dd10", float("nan")),
            }
        )
    return pd.DataFrame(rows).sort_values("目前Day")


def prelisting_backtest_table() -> pd.DataFrame:
    rows = []
    for dataset_name, stats_map, note in (
        ("全樣本", PRELISTING_BACKTEST_STATS, "不使用掛牌前20日漲幅條件；掛牌日前買進，掛牌日出場"),
    ):
        for day, stats in stats_map.items():
            rows.append(
                {
                    "樣本": dataset_name,
                    "買進日": day,
                    "出場日": 0,
                    "樣本數": stats["events"],
                    "平均報酬": stats["avg"],
                    "中位數": stats["median"],
                    "勝率": stats["win"],
                    "備註": note,
                }
            )
    return pd.DataFrame(rows).sort_values(["樣本", "買進日"])


def tminus20_exit_rules_table() -> pd.DataFrame:
    return pd.DataFrame(TMINUS20_EXIT_RULE_STATS)


def main() -> None:
    inject_custom_css()
    st.title("台灣可轉債 CB 戰術監控")

    with st.sidebar:
        st.subheader("資料設定")
        secret_token = read_secret_finlab_token()
        env_token = os.environ.get("FINLAB_API_TOKEN", "").strip()
        configured_token = secret_token or env_token
        configured_token_available = bool(configured_token)
        finlab_token_input = st.text_input(
            "FinLab API Token",
            type="password",
            placeholder="可留空，改讀 secrets 或 FINLAB_API_TOKEN",
            help="Token 只會放在目前 Streamlit 行程記憶體，不會寫入程式檔。",
        )
        finlab_token = finlab_token_input.strip() or configured_token
        if configured_token_available and not finlab_token_input:
            source = "Streamlit secrets" if secret_token else "環境變數 FINLAB_API_TOKEN"
            st.caption(f"已偵測到 {source}。")
        quote_mode = st.selectbox(
            "現股報價來源",
            ["FinLab 優先", "只用 FinLab", "只用 yfinance"],
            index=0,
        )
        enable_backtest_strategy = st.toggle(
            "啟用強勢續拉策略標記",
            value=True,
            help="已掛牌標的使用完整回測條件；新公告只做候選預警，20日動能以目前可得資料計算。",
        )
        use_google = st.toggle("啟用 Google News 關鍵字 RSS", value=True)
        extra_feeds = st.text_area(
            "額外 RSS URL",
            placeholder="每行一個 RSS URL",
            height=90,
        )
        max_official_rows = st.number_input("已掛牌標的上限", min_value=20, max_value=1000, value=300, step=20)
        if st.button("立即重新整理", type="primary", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    feed_urls = list(default_feed_urls())
    if not use_google:
        feed_urls = [url for url in feed_urls if "news.google.com" not in url]
    feed_urls.extend([line.strip() for line in extra_feeds.splitlines() if line.strip()])

    with st.spinner("整合櫃買 OpenAPI、RSS 重訊與現股報價中..."):
        official_df, official_issues = fetch_tpex_cb()
        new_df, news_issues = fetch_new_pricing_cases(tuple(feed_urls))
        new_df = ensure_columns(new_df)
        official_df = ensure_columns(official_df).head(int(max_official_rows))
        combined = pd.concat([new_df, official_df], ignore_index=True)
        dashboard_df, quote_issues = enrich_with_quotes(combined, finlab_token=finlab_token, quote_mode=quote_mode)
        dashboard_df, strategy_issues = apply_backtest_strategy(
            dashboard_df,
            finlab_token=finlab_token,
            enabled=enable_backtest_strategy,
        )

    render_warnings(news_issues + official_issues + quote_issues + strategy_issues)

    new_count = int((dashboard_df["標記"] == "新定價案").sum()) if "標記" in dashboard_df.columns else 0
    st.caption(
        f"更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜"
        f"新定價案 {new_count} 筆已強制置頂｜官方掛牌庫 {len(official_df)} 筆"
    )

    st.subheader("監控總覽")
    control_cols = st.columns([2.0, 1.05, 1.65, 1.55, 1.25])
    with control_cols[0]:
        search_text = st.text_input(
            "搜尋",
            placeholder="輸入股票代號、CB代碼、名稱或重訊關鍵字",
            label_visibility="collapsed",
        )
    with control_cols[1]:
        tag_filter = st.multiselect("標記", ["全部", "新定價案", "已掛牌"], default=["全部"])
    with control_cols[2]:
        strategy_filter = st.multiselect(
            "策略訊號",
            [
                "全部",
                STRATEGY_SIGNAL_STRONG,
                STRATEGY_SIGNAL_CANDIDATE,
                STRATEGY_SIGNAL_OVERHEAT,
                STRATEGY_SIGNAL_PRESSURE,
                STRATEGY_SIGNAL_NORMAL,
            ],
            default=["全部"],
        )
    with control_cols[3]:
        class_filter = st.multiselect(
            "分類",
            ["全部", "🚀 拉抬攻擊", "🟦 疑似壓價", "⚪ 暫無報價", "⏳ 觀察標的"],
            default=["全部"],
        )
    with control_cols[4]:
        quote_filter = st.multiselect("報價來源", ["全部", "FinLab", "yfinance"], default=["全部"])

    filtered_df = filter_dashboard(dashboard_df, search_text, tag_filter, class_filter, strategy_filter, quote_filter)
    render_status_chips(dashboard_df, filtered_df)

    render_metrics(filtered_df)

    display_df = filtered_df[[col for col in DISPLAY_COLUMNS if col in filtered_df.columns]].copy()
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=640,
        column_config={
            "標記": st.column_config.TextColumn("標記", width="small"),
            "策略訊號": st.column_config.TextColumn("策略訊號", width="medium"),
            "分類": st.column_config.TextColumn("分類", width="medium"),
            "CB代碼": st.column_config.TextColumn("CB代碼", width="small"),
            "CB名稱": st.column_config.TextColumn("CB名稱", width="medium"),
            "股票代號": st.column_config.TextColumn("股票代號", width="small"),
            "報價來源": st.column_config.TextColumn("報價來源", width="small"),
            "報價代碼": st.column_config.TextColumn("報價代碼", width="small"),
            "轉換價": st.column_config.NumberColumn("轉換價", format="%.2f"),
            "現股價格": st.column_config.NumberColumn("現股價格", format="%.2f"),
            "轉換價值": st.column_config.NumberColumn("轉換價值", format="%.2f"),
            "溢折價率": st.column_config.NumberColumn("溢折價率", format="%.2f%%"),
            "市值(億)": st.column_config.NumberColumn("市值(億)", format="%.2f"),
            "事件溢價率": st.column_config.NumberColumn("事件溢價率", format="%.2f%%"),
            "掛牌前20交易日漲幅": st.column_config.NumberColumn("掛牌前20交易日漲幅", format="%.2f%%"),
            "20日動能錨點": st.column_config.TextColumn("20日動能錨點", width="small"),
            "5日量比": st.column_config.NumberColumn("5日量比", format="%.2f"),
            "掛牌日量比": st.column_config.NumberColumn("掛牌日量比", format="%.2f"),
            "Day起算日": st.column_config.TextColumn("Day起算日", width="small"),
            "事件日來源": st.column_config.TextColumn("事件日來源", width="small"),
            "定價公告日": st.column_config.TextColumn("定價公告日", width="small"),
            "預計掛牌日": st.column_config.TextColumn("預計掛牌日", width="small"),
            "目前Day": st.column_config.NumberColumn("目前Day", format="%d"),
            "目前Day顯示": st.column_config.TextColumn("目前Day", width="small"),
            "買進時機": st.column_config.TextColumn("買進時機", width="medium"),
            "掛牌前後階段": st.column_config.TextColumn("掛牌前後階段", width="medium"),
            "回測樣本數": st.column_config.NumberColumn("回測樣本數", format="%d"),
            "回測勝率": st.column_config.NumberColumn("回測勝率", format="%.2f%%"),
            "回測中位數": st.column_config.NumberColumn("回測中位數", format="%.2f%%"),
            "回測平均報酬": st.column_config.NumberColumn("回測平均報酬", format="%.2f%%"),
            "回測平均最大不利": st.column_config.NumberColumn("回測平均最大不利", format="%.2f%%"),
            "策略錨定日": st.column_config.TextColumn("策略錨定日", width="small"),
            "策略備註": st.column_config.TextColumn("策略備註", width="large"),
            "上市日期": st.column_config.TextColumn("上市日期", width="small"),
            "到期日期": st.column_config.TextColumn("到期日期", width="small"),
            "重訊連結": st.column_config.LinkColumn("重訊連結"),
        },
    )

    with st.expander("戰術分類邏輯"):
        st.markdown(
            """
            - 🚀 拉抬攻擊：現股溢價 > 2%。
            - 🟦 疑似壓價：現股折價介於 -0.5% 到 -12% 之間。
            - ⚪ 暫無報價：現股價格為 0，或 FinLab / yfinance 都無法取得報價。
            - ⏳ 觀察標的：其餘區間。

            轉換價值 = `(100 / 轉換價) * 現股價格`

            溢折價率 = `((現股價格 / 轉換價) - 1) * 100%`
            """
        )

    with st.expander("強勢續拉策略條件"):
        st.markdown(
            """
            這組條件來自 CB 掛牌事件回測。回測參數為 `--start-year 2019`，
            TPEx 有效回測樣本區間為 `2021-07-27` 至 `2026-06-18`，共 371 筆；
            最佳嚴格條件樣本為 `2024-07-01` 至 `2026-05-29`，共 12 筆。

            - 🔥 強勢續拉型：只適用已掛牌事件。條件為市值 >= 80 億、掛牌日前 20 交易日漲幅 >= 20%、事件溢價率 >= 2%、5 日量比 <= 2、掛牌日量比 <= 1。
            - 🟡 強勢續拉候選：只作為新公告預警。新定價案尚未完整知道掛牌日量比，若也缺預計掛牌日，20 日動能會以最新交易日作為候選觀察錨點。
            - ⚠️ 過熱出貨疑慮：動能與價差符合，但 5 日量比 > 2 或掛牌日量比 > 1。
            - 🟦 壓價觀察型：事件溢價率介於 -12% 到 2%，偏向轉換價附近觀察。

            `目前Day` 是交易日計算：Day 0 為 Day 起算日後第一個可交易日，可直接對應回測的 T+10、T+20。
            若掛牌日在未來，`目前Day` 會顯示為 T-10、T-5 等掛牌前交易日。
            已掛牌標的的 Day 起算日使用上市/掛牌日；新定價案若爬到預計掛牌日，會優先用預計掛牌日，
            爬不到才退回定價公告日，並以 `事件日來源` 標示。
            因此新定價案若顯示 T-5，代表距預計掛牌約 5 個交易日；若顯示 T+3 且來源為定價公告日，
            代表定價公告已發生 3 個交易日，不代表 CB 已掛牌。
            策略錨定日：已掛牌標的使用掛牌日後第一個交易日；新定價案或未完成掛牌資料使用最新可得交易日。
            因此若目標是掛牌前 T-20 進場，不能用「掛牌日前 20 交易日漲幅 >= 20%」當事前篩選；
            那是掛牌日回看才知道的條件。新公告階段只能用「截至今天的近 20 交易日動能」做預警。
            """
        )

    with st.expander("買進時機回測機率"):
        st.markdown(
            """
            下表使用「強勢續拉型」12 筆樣本，假設在指定 Day 買進，固定於 T+20 出場。
            T+20 本身是固定觀察出場點，買進日與出場日相同會機械性得到 0%，因此不列入買進統計。
            這張表只適合用在掛牌日條件確認後的 T+0 之後買進；若要掛牌前 T-20 買，不能使用這組事後篩選。
            表格中的最大不利為買進後到 T+20 前的平均最大不利報酬，回撤機率則統計期間內曾跌破 -5% 或 -10% 的比例。
            勝率若出現相同值，通常是因為樣本只有 12 筆，1 筆事件就等於 8.33 個百分點。

            - T+0~T+3：早期觀察，不重倉。
            - T+4~T+8：洗盤確認期。
            - T+9~T+11：最佳切入窗。
            - T+12~T+19：續拉/停利窗。
            - T+20：固定觀察出場點，不列為買點。
            - T+20 後：不追，改看續航。
            """
        )

    with st.expander("掛牌前交易回測"):
        st.markdown(
            """
            下表測試「掛牌日前 N 個交易日買進，掛牌日 T+0 出場」。
            這裡只保留全樣本，不加入「掛牌前 20 交易日漲幅 >= 20%」條件，避免用掛牌日才知道的資訊回頭篩選 T-20 買點。
            但若 T-20 當天尚未有 CB 定價公告，這仍是事件研究，不等於可直接執行的公告日策略。

            實務解讀：
            - T-20~T-11：預先反應早段，報酬最大但較難提前確認。
            - T-10~T-5：預掛牌強勢窗。
            - T-3~T-1：太接近掛牌，歷史優勢快速下降。
            """
        )
        st.dataframe(
            prelisting_backtest_table(),
            use_container_width=True,
            hide_index=True,
            column_config={
                "樣本": st.column_config.TextColumn("樣本", width="medium"),
                "買進日": st.column_config.NumberColumn("買進日", format="T%d"),
                "出場日": st.column_config.NumberColumn("出場日", format="T+%d"),
                "樣本數": st.column_config.NumberColumn("樣本數", format="%d"),
                "平均報酬": st.column_config.NumberColumn("平均報酬", format="%.2f%%"),
                "中位數": st.column_config.NumberColumn("中位數", format="%.2f%%"),
                "勝率": st.column_config.NumberColumn("勝率", format="%.2f%%"),
                "備註": st.column_config.TextColumn("備註", width="large"),
            },
        )
        st.markdown(
            """
            **T-20 買進後的出場規則**

            下表同樣使用全樣本，不加入掛牌日前 20 日漲幅條件。固定 T-5/T-3 出場的報酬較高；
            停利停損規則會降低深度回撤機率，但也會提早賣掉部分後續轉強的案例。
            """
        )
        st.dataframe(
            tminus20_exit_rules_table(),
            use_container_width=True,
            hide_index=True,
            column_config={
                "規則": st.column_config.TextColumn("規則", width="large"),
                "樣本數": st.column_config.NumberColumn("樣本數", format="%d"),
                "平均報酬": st.column_config.NumberColumn("平均報酬", format="%.2f%%"),
                "中位數": st.column_config.NumberColumn("中位數", format="%.2f%%"),
                "勝率": st.column_config.NumberColumn("勝率", format="%.2f%%"),
                "平均最大不利": st.column_config.NumberColumn("平均最大不利", format="%.2f%%"),
                "回撤<=-5%機率": st.column_config.NumberColumn("回撤<=-5%機率", format="%.2f%%"),
                "回撤<=-10%機率": st.column_config.NumberColumn("回撤<=-10%機率", format="%.2f%%"),
                "平均出場Day": st.column_config.NumberColumn("平均出場Day", format="T%.1f"),
            },
        )
        st.dataframe(
            entry_day_backtest_table(),
            use_container_width=True,
            hide_index=True,
            column_config={
                "目前Day": st.column_config.NumberColumn("目前Day", format="T+%d"),
                "買進時機": st.column_config.TextColumn("買進時機", width="medium"),
                "樣本數": st.column_config.NumberColumn("樣本數", format="%d"),
                "平均報酬": st.column_config.NumberColumn("平均報酬", format="%.2f%%"),
                "中位數": st.column_config.NumberColumn("中位數", format="%.2f%%"),
                "勝率": st.column_config.NumberColumn("勝率", format="%.2f%%"),
                "平均最大不利": st.column_config.NumberColumn("平均最大不利", format="%.2f%%"),
                "回撤<=-5%機率": st.column_config.NumberColumn("回撤<=-5%機率", format="%.2f%%"),
                "回撤<=-10%機率": st.column_config.NumberColumn("回撤<=-10%機率", format="%.2f%%"),
            },
        )


if __name__ == "__main__":
    main()
