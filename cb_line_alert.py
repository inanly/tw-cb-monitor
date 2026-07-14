from __future__ import annotations

import argparse
import os
import re
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests

try:
    from urllib3.exceptions import InsecureRequestWarning

    warnings.simplefilter("ignore", InsecureRequestWarning)
except Exception:
    pass


TPEX_CB_URL = "https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data"
TPEX_RECENT_CB_LISTED_URL = "https://www.tpex.org.tw/www/zh-tw/bond/convSearch"
TPEX_STOCK_TRADING_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
}


@dataclass(frozen=True)
class StrongConfig:
    market_cap_min_yi: float = 80.0
    pre_return_min_pct: float = 20.0
    conversion_gap_min_pct: float = 2.0
    pre_volume_ratio_max: float = 2.0
    event_volume_ratio_max: float = 1.0


def clean_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except Exception:
        return 0.0


def parse_yyyymmdd(value: Any) -> pd.Timestamp:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) != 8:
        return pd.NaT
    try:
        return pd.Timestamp(datetime.strptime(text, "%Y%m%d").date())
    except ValueError:
        return pd.NaT


def parse_roc_date(value: Any) -> pd.Timestamp:
    text = str(value or "").strip()
    match = re.search(r"(\d{2,4})[./-](\d{1,2})[./-](\d{1,2})", text)
    if not match:
        return pd.NaT
    year = int(match.group(1))
    if year < 1911:
        year += 1911
    try:
        return pd.Timestamp(year=year, month=int(match.group(2)), day=int(match.group(3)))
    except ValueError:
        return pd.NaT


def parse_target_date(value: str | None) -> pd.Timestamp:
    if value:
        return pd.Timestamp(value).normalize()
    return pd.Timestamp(datetime.now(TAIPEI_TZ).date())


def extract_query_value(url: str, key: str) -> str:
    try:
        values = parse_qs(urlparse(url).query).get(key, [])
        return str(values[0]).strip() if values else ""
    except Exception:
        return ""


def extract_mops_conversion_price(text: str) -> float:
    cleaned = re.sub(r"\s+", " ", text)
    patterns = [
        r"發行時轉\(交\)換價格[：:\s]*([0-9,]+(?:\.\d+)?)\s*元",
        r"最新轉\(交\)換價格[：:\s]*([0-9,]+(?:\.\d+)?)\s*元",
        r"轉\(交\)換價格[：:\s]*([0-9,]+(?:\.\d+)?)\s*元",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            price = clean_float(match.group(1))
            if price > 0:
                return price
    return 0.0


def fetch_tpex_cb_events() -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(TPEX_CB_URL, headers=HEADERS, timeout=45, verify=False)
            response.raise_for_status()
            raw = response.json()
            break
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"TPEx CB OpenAPI failed after retries: {last_error}") from last_error

    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        cb_code = str(item.get("BondCode", "")).strip()
        stock_code = str(item.get("IssuerCode", "")).strip()
        listing_date = parse_yyyymmdd(item.get("ListingDate"))
        conversion_price = clean_float(item.get("Conversion/ExchangePriceAtIssuance"))
        if not cb_code or not stock_code.isdigit() or len(stock_code) != 4 or pd.isna(listing_date):
            continue

        rows.append(
            {
                "cb_code": cb_code,
                "cb_name": str(item.get("ShortName") or item.get("IssuerName") or cb_code).strip(),
                "stock_code": stock_code,
                "issuer": str(item.get("IssuerName", "")).strip(),
                "listing_date": listing_date,
                "conversion_price": conversion_price,
            }
        )

    return pd.DataFrame(rows)


def fetch_tpex_recent_listed_cb_events() -> pd.DataFrame:
    payload = {"name": "bondIssuer", "searchNo": "", "response": "json"}
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.post(
                TPEX_RECENT_CB_LISTED_URL,
                data=payload,
                headers=HEADERS,
                timeout=45,
                verify=False,
            )
            response.raise_for_status()
            raw = response.json()
            break
        except Exception as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"TPEx recent listed CB API failed after retries: {last_error}") from last_error

    table = (raw.get("tables") or [{}])[0]
    fields = table.get("fields") or []
    rows: list[dict[str, Any]] = []
    for item in table.get("data") or []:
        if not isinstance(item, list) or len(item) < len(fields):
            continue
        row = dict(zip(fields, item))
        stock_code = str(row.get("發行機構代碼", "")).strip()
        issue_link = str(row.get("發行資料", "")).strip()
        cb_code = extract_query_value(issue_link, "bond_id")
        listing_date = parse_roc_date(row.get("掛牌日期", ""))
        if not cb_code or not stock_code.isdigit() or len(stock_code) != 4 or pd.isna(listing_date):
            continue
        rows.append(
            {
                "cb_code": cb_code,
                "cb_name": str(row.get("債券名稱") or cb_code).strip(),
                "stock_code": stock_code,
                "issuer": str(row.get("發行機構名稱", "")).strip(),
                "listing_date": listing_date,
                "conversion_price": float("nan"),
                "issue_link": issue_link,
                "listing_source": "TPEx最近上櫃頁",
            }
        )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(subset=["cb_code", "stock_code", "listing_date"])


def fetch_mops_conversion_price(url: str) -> float:
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except Exception:
        return 0.0
    text = re.sub(r"<[^>]+>", " ", response.text)
    return extract_mops_conversion_price(text)


def fill_conversion_prices_from_master(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events
    result = events.copy()

    try:
        master = fetch_tpex_cb_events()
    except Exception:
        master = pd.DataFrame()

    if not master.empty:
        master_lookup = master.drop_duplicates("cb_code").set_index("cb_code")
        for idx, row in result.iterrows():
            cb_code = str(row.get("cb_code", ""))
            if cb_code not in master_lookup.index:
                continue
            master_row = master_lookup.loc[cb_code]
            if not is_number(result.at[idx, "conversion_price"]) or float(result.at[idx, "conversion_price"]) <= 0:
                result.at[idx, "conversion_price"] = float(master_row.get("conversion_price", float("nan")))
            if not str(result.loc[idx].get("cb_name", "")).strip():
                result.at[idx, "cb_name"] = str(master_row.get("cb_name", ""))
            if not str(result.loc[idx].get("issuer", "")).strip():
                result.at[idx, "issuer"] = str(master_row.get("issuer", ""))

    issue_links = result["issue_link"] if "issue_link" in result.columns else pd.Series("", index=result.index)
    missing = result[(~pd.to_numeric(result["conversion_price"], errors="coerce").gt(0)) & issue_links.astype(str).str.startswith("http")]
    for idx, row in missing.iterrows():
        price = fetch_mops_conversion_price(str(row.get("issue_link", "")))
        if price > 0:
            result.at[idx, "conversion_price"] = price

    return result


def finlab_wide_to_pandas(frame: Any) -> pd.DataFrame:
    df = pd.DataFrame(frame)
    df.index = pd.to_datetime(df.index)
    df.columns = [str(col) for col in df.columns]
    return df.sort_index()


def login_finlab() -> Any:
    token = os.environ.get("FINLAB_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing FINLAB_API_TOKEN")

    import finlab
    from finlab import data

    finlab.login(token)
    return data


def latest_at_or_before(frame: pd.DataFrame, code: str, date: pd.Timestamp) -> float:
    if frame is None or frame.empty or code not in frame.columns:
        return float("nan")
    series = pd.to_numeric(frame[code], errors="coerce").dropna()
    if series.empty:
        return float("nan")
    pos = int(series.index.searchsorted(date, side="right") - 1)
    if pos < 0:
        return float("nan")
    return float(series.iloc[pos])


def average_before(series: pd.Series, pos: int, window: int, min_count: int) -> float:
    if pos <= 0:
        return float("nan")
    start = max(0, pos - window)
    values = pd.to_numeric(series.iloc[start:pos], errors="coerce").dropna()
    if len(values) < min_count:
        return float("nan")
    return float(values.mean())


def is_number(value: Any) -> bool:
    try:
        return pd.notna(value) and float(value) == float(value)
    except Exception:
        return False


def pass_min(value: Any, threshold: float) -> bool:
    return is_number(value) and float(value) >= threshold


def pass_max(value: Any, threshold: float) -> bool:
    return is_number(value) and float(value) <= threshold


def market_cap_to_yi(value: float) -> float:
    if not is_number(value):
        return float("nan")
    return float(value) / 100_000_000


def month_start_values(target_date: pd.Timestamp, months_back: int = 2) -> list[str]:
    month_start = pd.Timestamp(target_date).replace(day=1)
    values = []
    for offset in range(months_back, -1, -1):
        values.append((month_start - pd.DateOffset(months=offset)).strftime("%Y/%m/01"))
    return values


def fetch_tpex_stock_history(stock_code: str, target_date: pd.Timestamp) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for month_value in month_start_values(target_date, months_back=2):
        try:
            response = requests.get(
                TPEX_STOCK_TRADING_URL,
                params={"code": stock_code, "date": month_value, "response": "json"},
                headers=HEADERS,
                timeout=30,
                verify=False,
            )
            response.raise_for_status()
            raw = response.json()
        except Exception:
            continue

        table = (raw.get("tables") or [{}])[0] if isinstance(raw, dict) else {}
        fields = table.get("fields") or []
        for item in table.get("data") or []:
            if not isinstance(item, list) or len(item) < len(fields):
                continue
            row = dict(zip(fields, item))
            trade_date = parse_roc_date(row.get("日 期", ""))
            close = clean_float(row.get("收盤"))
            volume = clean_float(row.get("成交張數"))
            if pd.isna(trade_date) or close <= 0:
                continue
            rows.append({"date": trade_date, "close": close, "volume": volume})

    if not rows:
        return pd.DataFrame(columns=["close", "volume"])

    df = pd.DataFrame(rows).drop_duplicates("date", keep="last").sort_values("date")
    return df.set_index("date")


def fetch_tpex_market_cap_yi(stock_code: str) -> float:
    try:
        response = requests.get(TPEX_DAILY_QUOTES_URL, headers=HEADERS, timeout=45, verify=False)
        response.raise_for_status()
        raw = response.json()
    except Exception:
        return float("nan")

    if not isinstance(raw, list):
        return float("nan")

    for item in raw:
        if not isinstance(item, dict) or str(item.get("SecuritiesCompanyCode", "")).strip() != stock_code:
            continue
        close = clean_float(item.get("Close"))
        shares = clean_float(item.get("Capitals"))
        if close > 0 and shares > 0:
            return close * shares / 100_000_000
    return float("nan")


def compute_listing_day_factors_public(
    events: pd.DataFrame,
    target_date: pd.Timestamp,
    config: StrongConfig,
    reason: str = "",
) -> tuple[pd.DataFrame, list[str]]:
    issues: list[str] = []
    if reason:
        issues.append(f"FinLab 資料讀取失敗，已改用櫃買公開資料備援：{reason}")

    rows: list[dict[str, Any]] = []
    for event in events.itertuples(index=False):
        stock_code = str(event.stock_code)
        history = fetch_tpex_stock_history(stock_code, target_date)
        if history.empty or stock_code == "":
            issues.append(f"{stock_code} 找不到櫃買日成交資料，無法備援判斷")
            continue

        close_series = pd.to_numeric(history["close"], errors="coerce").dropna()
        volume_series = pd.to_numeric(history["volume"], errors="coerce").dropna()
        pos = int(close_series.index.searchsorted(target_date, side="left"))
        if pos >= len(close_series) or close_series.index[pos].normalize() != target_date:
            issues.append(f"{stock_code} {target_date.date()} 櫃買收盤價尚未更新，略過")
            continue
        if pos < 20:
            issues.append(f"{stock_code} 櫃買歷史資料不足 20 交易日")
            continue

        anchor_price = float(close_series.iloc[pos])
        prior_price = float(close_series.iloc[pos - 20])
        conversion_price = float(event.conversion_price)
        pre_return_pct = (anchor_price / prior_price - 1) * 100 if prior_price > 0 else float("nan")
        conversion_gap_pct = (anchor_price / conversion_price - 1) * 100 if conversion_price > 0 else float("nan")

        pre_volume_ratio = float("nan")
        event_volume_ratio = float("nan")
        volume_pos = int(volume_series.index.searchsorted(target_date, side="left"))
        if volume_pos < len(volume_series) and volume_series.index[volume_pos].normalize() == target_date:
            pre_20d_avg_volume = average_before(volume_series, volume_pos, 20, 10)
            pre_5d_avg_volume = average_before(volume_series, volume_pos, 5, 3)
            event_volume = float(volume_series.iloc[volume_pos])
            if is_number(pre_20d_avg_volume) and pre_20d_avg_volume > 0:
                pre_volume_ratio = pre_5d_avg_volume / pre_20d_avg_volume
                event_volume_ratio = event_volume / pre_20d_avg_volume

        market_cap_yi = fetch_tpex_market_cap_yi(stock_code)
        checks = {
            "市值>=80億": pass_min(market_cap_yi, config.market_cap_min_yi),
            "掛牌前20日漲幅>=20%": pass_min(pre_return_pct, config.pre_return_min_pct),
            "事件溢價>=2%": pass_min(conversion_gap_pct, config.conversion_gap_min_pct),
            "5日量比<=2": pass_max(pre_volume_ratio, config.pre_volume_ratio_max),
            "掛牌日量比<=1": pass_max(event_volume_ratio, config.event_volume_ratio_max),
        }
        strong = all(checks.values())
        failed = [name for name, ok in checks.items() if not ok]

        rows.append(
            {
                "strong": strong,
                "failed": "、".join(failed),
                "cb_code": str(event.cb_code),
                "cb_name": str(event.cb_name),
                "stock_code": stock_code,
                "issuer": str(event.issuer),
                "listing_date": target_date.date().isoformat(),
                "conversion_price": conversion_price,
                "stock_price": anchor_price,
                "market_cap_yi": market_cap_yi,
                "pre_return_pct": pre_return_pct,
                "conversion_gap_pct": conversion_gap_pct,
                "pre_volume_ratio": pre_volume_ratio,
                "event_volume_ratio": event_volume_ratio,
            }
        )

    return pd.DataFrame(rows), issues


def compute_listing_day_factors(
    events: pd.DataFrame,
    target_date: pd.Timestamp,
    config: StrongConfig,
) -> tuple[pd.DataFrame, list[str]]:
    issues: list[str] = []
    if events.empty:
        return pd.DataFrame(), issues

    try:
        data = login_finlab()
        data.truncate_start = (target_date - timedelta(days=180)).strftime("%Y-%m-%d")
        data.truncate_end = target_date.strftime("%Y-%m-%d")

        close = finlab_wide_to_pandas(data.get("price:收盤價"))
        volume = finlab_wide_to_pandas(data.get("price:成交股數"))
        market_value = finlab_wide_to_pandas(data.get("etl:market_value"))
    except Exception as exc:
        return compute_listing_day_factors_public(events, target_date, config, str(exc))

    rows: list[dict[str, Any]] = []
    for event in events.itertuples(index=False):
        stock_code = str(event.stock_code)
        if stock_code not in close.columns:
            issues.append(f"{stock_code} 找不到 FinLab 收盤價")
            continue

        close_series = pd.to_numeric(close[stock_code], errors="coerce").dropna()
        volume_series = pd.to_numeric(volume.get(stock_code, pd.Series(dtype=float)), errors="coerce").dropna()
        if close_series.empty:
            continue

        pos = int(close_series.index.searchsorted(target_date, side="left"))
        if pos >= len(close_series) or close_series.index[pos].normalize() != target_date:
            issues.append(f"{stock_code} {target_date.date()} 收盤價尚未更新，略過完整強勢判斷")
            continue
        if pos < 20:
            issues.append(f"{stock_code} 歷史資料不足 20 交易日")
            continue

        anchor_price = float(close_series.iloc[pos])
        prior_price = float(close_series.iloc[pos - 20])
        conversion_price = float(event.conversion_price)

        pre_return_pct = (anchor_price / prior_price - 1) * 100 if prior_price > 0 else float("nan")
        conversion_gap_pct = (anchor_price / conversion_price - 1) * 100 if conversion_price > 0 else float("nan")

        pre_volume_ratio = float("nan")
        event_volume_ratio = float("nan")
        if not volume_series.empty:
            volume_pos = int(volume_series.index.searchsorted(target_date, side="left"))
            if volume_pos < len(volume_series) and volume_series.index[volume_pos].normalize() == target_date:
                pre_20d_avg_volume = average_before(volume_series, volume_pos, 20, 10)
                pre_5d_avg_volume = average_before(volume_series, volume_pos, 5, 3)
                event_volume = float(volume_series.iloc[volume_pos])
                if is_number(pre_20d_avg_volume) and pre_20d_avg_volume > 0:
                    pre_volume_ratio = pre_5d_avg_volume / pre_20d_avg_volume
                    event_volume_ratio = event_volume / pre_20d_avg_volume

        market_cap_yi = market_cap_to_yi(latest_at_or_before(market_value, stock_code, target_date))

        checks = {
            "市值>=80億": pass_min(market_cap_yi, config.market_cap_min_yi),
            "掛牌前20日漲幅>=20%": pass_min(pre_return_pct, config.pre_return_min_pct),
            "事件溢價>=2%": pass_min(conversion_gap_pct, config.conversion_gap_min_pct),
            "5日量比<=2": pass_max(pre_volume_ratio, config.pre_volume_ratio_max),
            "掛牌日量比<=1": pass_max(event_volume_ratio, config.event_volume_ratio_max),
        }
        strong = all(checks.values())
        failed = [name for name, ok in checks.items() if not ok]

        rows.append(
            {
                "strong": strong,
                "failed": "、".join(failed),
                "cb_code": str(event.cb_code),
                "cb_name": str(event.cb_name),
                "stock_code": stock_code,
                "issuer": str(event.issuer),
                "listing_date": target_date.date().isoformat(),
                "conversion_price": conversion_price,
                "stock_price": anchor_price,
                "market_cap_yi": market_cap_yi,
                "pre_return_pct": pre_return_pct,
                "conversion_gap_pct": conversion_gap_pct,
                "pre_volume_ratio": pre_volume_ratio,
                "event_volume_ratio": event_volume_ratio,
            }
        )

    if not rows:
        public_result, public_issues = compute_listing_day_factors_public(
            events,
            target_date,
            config,
            "FinLab 尚無完整掛牌日資料",
        )
        return public_result, issues + public_issues

    return pd.DataFrame(rows), issues


def format_pct(value: Any) -> str:
    return "NA" if not is_number(value) else f"{float(value):.2f}%"


def format_num(value: Any) -> str:
    return "NA" if not is_number(value) else f"{float(value):.2f}"


def build_message(target_date: pd.Timestamp, listing_count: int, result: pd.DataFrame, issues: list[str], send_no_match: bool) -> str:
    strong = result[result["strong"]] if not result.empty and "strong" in result.columns else pd.DataFrame()
    date_text = target_date.date().isoformat()

    if strong.empty and not send_no_match:
        return ""

    lines: list[str] = []
    if strong.empty:
        lines.append(f"CB掛牌日強勢條件監控 {date_text}")
        lines.append(f"今日掛牌CB：{listing_count} 檔")
        lines.append("沒有符合完整強勢條件的標的。")
    else:
        lines.append(f"CB強勢掛牌警報 {date_text}")
        lines.append(f"命中 {len(strong)} / 今日掛牌 {listing_count} 檔")
        for row in strong.itertuples(index=False):
            lines.extend(
                [
                    "",
                    f"{row.cb_code} {row.cb_name} / {row.stock_code} {row.issuer}",
                    f"現股 {format_num(row.stock_price)}，轉換價 {format_num(row.conversion_price)}",
                    f"市值 {format_num(row.market_cap_yi)} 億",
                    f"掛牌前20日漲幅 {format_pct(row.pre_return_pct)}",
                    f"事件溢價 {format_pct(row.conversion_gap_pct)}",
                    f"5日量比 {format_num(row.pre_volume_ratio)}，掛牌日量比 {format_num(row.event_volume_ratio)}",
                ]
            )

    if issues:
        lines.append("")
        lines.append("資料提醒：")
        lines.extend(f"- {issue}" for issue in issues[:6])
        if len(issues) > 6:
            lines.append(f"- 另有 {len(issues) - 6} 則提醒")

    return "\n".join(lines)


def chunk_text(text: str, limit: int = 4500) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def push_line_message(text: str) -> None:
    channel_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    to_id = os.environ.get("LINE_TO_ID", "").strip()
    if not channel_token or not to_id:
        raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN or LINE_TO_ID")

    headers = {
        "Authorization": f"Bearer {channel_token}",
        "Content-Type": "application/json",
    }
    for chunk in chunk_text(text):
        payload = {"to": to_id, "messages": [{"type": "text", "text": chunk}]}
        response = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()


def truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Send LINE alert when today's listed CBs hit strong listing-day conditions.")
    parser.add_argument("--date", default="", help="Target listing date in YYYY-MM-DD. Default: today in Asia/Taipei.")
    parser.add_argument("--dry-run", action="store_true", help="Print message instead of sending LINE push.")
    parser.add_argument("--send-no-match", action="store_true", help="Send a LINE message even when there is no match.")
    args = parser.parse_args()

    target_date = parse_target_date(args.date or None)
    send_no_match = args.send_no_match or truthy_env("LINE_SEND_NO_MATCH")

    events = fetch_tpex_recent_listed_cb_events()
    todays = events[events["listing_date"].dt.normalize() == target_date].copy() if not events.empty else pd.DataFrame()
    if todays.empty:
        message = build_message(target_date, 0, pd.DataFrame(), [], send_no_match)
        if message and not args.dry_run:
            push_line_message(message)
        elif message:
            print(message)
        else:
            print(f"{target_date.date()} no CB listings; no LINE message sent.")
        return 0

    todays = fill_conversion_prices_from_master(todays)
    result, issues = compute_listing_day_factors(todays, target_date, StrongConfig())
    message = build_message(target_date, len(todays), result, issues, send_no_match)

    if not message:
        print(f"{target_date.date()} listed CBs={len(todays)}, strong matches=0; no LINE message sent.")
        return 0

    if args.dry_run:
        print(message)
    else:
        push_line_message(message)
        print(f"LINE message sent for {target_date.date()}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
