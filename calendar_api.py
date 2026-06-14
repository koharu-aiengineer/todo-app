import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_service = None


def _get_service():
    global _service
    if _service is not None:
        return _service

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise EnvironmentError("環境変数 GOOGLE_CREDENTIALS_JSON が設定されていません。")

    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    _service = build("calendar", "v3", credentials=creds)
    logger.info("Google Calendar サービスを初期化しました。")
    return _service


def _calendar_id() -> str:
    cal_id = os.environ.get("GOOGLE_CALENDAR_ID")
    if not cal_id:
        raise EnvironmentError("環境変数 GOOGLE_CALENDAR_ID が設定されていません。")
    return cal_id


def _build_event_body(title: str, due_date: str, due_time: str, due_end_time: str, content: str) -> dict:
    """時刻あり→時間指定イベント、なし→終日イベント。終了時刻未設定なら開始+1時間。"""
    if due_time:
        start_dt = datetime.fromisoformat(f"{due_date}T{due_time}")
        if due_end_time:
            end_dt = datetime.fromisoformat(f"{due_date}T{due_end_time}")
        else:
            end_dt = start_dt + timedelta(hours=1)
        return {
            "summary": title,
            "description": content,
            "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "Asia/Tokyo"},
            "end":   {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),   "timeZone": "Asia/Tokyo"},
        }
    return {
        "summary": title,
        "description": content,
        "start": {"date": due_date},
        "end":   {"date": due_date},
    }


def add_event(title: str, due_date: str, due_time: str = "", due_end_time: str = "", content: str = "") -> Optional[str]:
    """カレンダーにイベントを追加してイベントIDを返す。失敗時は None。"""
    try:
        service = _get_service()
        event = _build_event_body(title, due_date, due_time, due_end_time, content)
        result = service.events().insert(calendarId=_calendar_id(), body=event).execute()
        event_id = result.get("id")
        logger.info("カレンダーイベントを追加しました: event_id=%s", event_id)
        return event_id
    except Exception:
        logger.exception("カレンダーイベントの追加に失敗しました")
        return None


def update_event(event_id: str, title: str, due_date: str, due_time: str = "", due_end_time: str = "", content: str = "") -> bool:
    """カレンダーイベントを更新する。成功で True。"""
    try:
        service = _get_service()
        event = _build_event_body(title, due_date, due_time, due_end_time, content)
        service.events().update(
            calendarId=_calendar_id(), eventId=event_id, body=event
        ).execute()
        logger.info("カレンダーイベントを更新しました: event_id=%s", event_id)
        return True
    except Exception:
        logger.exception("カレンダーイベントの更新に失敗しました: event_id=%s", event_id)
        return False


def delete_event(event_id: str) -> bool:
    """カレンダーイベントを削除する。成功で True。"""
    try:
        service = _get_service()
        service.events().delete(calendarId=_calendar_id(), eventId=event_id).execute()
        logger.info("カレンダーイベントを削除しました: event_id=%s", event_id)
        return True
    except HttpError as e:
        if e.resp.status == 404:
            logger.warning("削除対象のイベントがすでに存在しません: event_id=%s", event_id)
            return True
        logger.exception("カレンダーイベントの削除に失敗しました: event_id=%s", event_id)
        return False
    except Exception:
        logger.exception("カレンダーイベントの削除に失敗しました: event_id=%s", event_id)
        return False
