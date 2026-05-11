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
    "上市日期",
    "到期日期",
    "資料來源",
    "重訊標題",
    "重訊連結",
    "更新時間",
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
    columns = list(dict.fromkeys(DISPLAY_COLUMNS + ["排序權重", "分類權重"]))
    return pd.DataFrame(columns=columns)


def ensure_columns(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        df = empty_cb_frame()
    elif not df.columns.is_unique:
        df = df.loc[:, ~df.columns.duplicated()].copy()

    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col != "轉換價" else 0.0

    for col in dict.fromkeys(DISPLAY_COLUMNS + ["排序權重", "分類權重"]):
        if col not in df.columns:
            df[col] = "" if col not in {"轉換價", "現股價格", "轉換價值", "溢折價率", "排序權重", "分類權重"} else 0.0

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

            if not is_potential_cb_pricing_text(full_text):
                continue

            stock_code, stock_name = extract_stock_identity(full_text)
            conversion_price = extract_conversion_price(full_text)

            if not is_new_pricing_case(full_text) or not stock_code or conversion_price <= 0:
                article_text = fetch_article_text(entry.get("link", ""))
                if article_text:
                    full_text = f"{full_text} {article_text}"

            stock_code, stock_name = extract_stock_identity(full_text)
            conversion_price = extract_conversion_price(full_text)
            if not is_new_pricing_case(full_text) or not stock_code or conversion_price <= 0:
                continue

            key = (stock_code, conversion_price, title)
            if key in seen:
                continue
            seen.add(key)

            rows.append(
                {
                    "CB代碼": "未掛牌",
                    "CB名稱": stock_name or f"{stock_code} 新定價案",
                    "股票代號": stock_code,
                    "轉換價": conversion_price,
                    "上市日期": "未上市",
                    "到期日期": "",
                    "標記": "新定價案",
                    "資料來源": "RSS 重訊",
                    "重訊標題": title,
                    "重訊連結": entry.get("link", ""),
                    "更新時間": entry.get("published", "") or entry.get("updated", ""),
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
    labels = ["🚀 拉抬攻擊", "🟦 疑似壓價", "⚪ 暫無報價", "⏳ 觀察標的"]
    cols = st.columns(4)
    for col, label in zip(cols, labels):
        col.metric(label, int(counts.get(label, 0)))


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
    quote_sources: list[str],
) -> pd.DataFrame:
    filtered = ensure_columns(df).copy()

    if tags and "全部" not in tags:
        filtered = filtered[filtered["標記"].isin(tags)]

    if classes and "全部" not in classes:
        filtered = filtered[filtered["分類"].isin(classes)]

    if quote_sources and "全部" not in quote_sources:
        filtered = filtered[filtered["報價來源"].isin(quote_sources)]

    query = search_text.strip().lower()
    if query:
        search_columns = ["CB代碼", "CB名稱", "股票代號", "標記", "分類", "資料來源", "重訊標題"]
        haystack = filtered[[col for col in search_columns if col in filtered.columns]].fillna("").astype(str)
        mask = haystack.apply(lambda col: col.str.lower().str.contains(re.escape(query), na=False)).any(axis=1)
        filtered = filtered[mask]

    return filtered.reset_index(drop=True)


def render_status_chips(df: pd.DataFrame, filtered_df: pd.DataFrame) -> None:
    total = len(df)
    visible = len(filtered_df)
    new_count = int((df["標記"] == "新定價案").sum()) if "標記" in df.columns else 0
    finlab_count = int((df["報價來源"] == "FinLab").sum()) if "報價來源" in df.columns else 0
    st.markdown(
        f"""
        <div class="cb-status">
            <span class="cb-chip">顯示 {visible} / {total} 筆</span>
            <span class="cb-chip">新定價案 {new_count} 筆</span>
            <span class="cb-chip">FinLab 報價 {finlab_count} 筆</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    inject_custom_css()
    st.title("台灣可轉債 CB 戰術監控")

    with st.sidebar:
        st.subheader("資料設定")
        env_token_available = bool(os.environ.get("FINLAB_API_TOKEN", "").strip())
        finlab_token_input = st.text_input(
            "FinLab API Token",
            type="password",
            placeholder="可留空，改讀 FINLAB_API_TOKEN",
            help="Token 只會放在目前 Streamlit 行程記憶體，不會寫入程式檔。",
        )
        finlab_token = finlab_token_input.strip() or os.environ.get("FINLAB_API_TOKEN", "").strip()
        if env_token_available and not finlab_token_input:
            st.caption("已偵測到環境變數 FINLAB_API_TOKEN。")
        quote_mode = st.selectbox(
            "現股報價來源",
            ["FinLab 優先", "只用 FinLab", "只用 yfinance"],
            index=0,
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

    render_warnings(news_issues + official_issues + quote_issues)

    new_count = int((dashboard_df["標記"] == "新定價案").sum()) if "標記" in dashboard_df.columns else 0
    st.caption(
        f"更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜"
        f"新定價案 {new_count} 筆已強制置頂｜官方掛牌庫 {len(official_df)} 筆"
    )

    st.subheader("監控總覽")
    control_cols = st.columns([2.2, 1.25, 1.65, 1.35])
    with control_cols[0]:
        search_text = st.text_input(
            "搜尋",
            placeholder="輸入股票代號、CB代碼、名稱或重訊關鍵字",
            label_visibility="collapsed",
        )
    with control_cols[1]:
        tag_filter = st.multiselect("標記", ["全部", "新定價案", "已掛牌"], default=["全部"])
    with control_cols[2]:
        class_filter = st.multiselect(
            "分類",
            ["全部", "🚀 拉抬攻擊", "🟦 疑似壓價", "⚪ 暫無報價", "⏳ 觀察標的"],
            default=["全部"],
        )
    with control_cols[3]:
        quote_filter = st.multiselect("報價來源", ["全部", "FinLab", "yfinance"], default=["全部"])

    filtered_df = filter_dashboard(dashboard_df, search_text, tag_filter, class_filter, quote_filter)
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


if __name__ == "__main__":
    main()
