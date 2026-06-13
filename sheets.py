"""
Google スプレッドシートを使った Todo データの永続化。

スプレッドシートの構造:
  シート名: todos
  列: todo_id | user_id | title | content | due_date | status | created_at

設計上の注意:
- gspread クライアントはモジュールレベルでキャッシュし、リクエストごとに
  再認証しない（OAuth トークンの有効期限内は再利用）。
- 全 CRUD 操作は get_all_values() で1回だけ全行取得してからメモリ上で
  処理することで、API 呼び出し回数を最小化する。
- Sheets API の書き込み系操作はレート制限 (60 req/min) があるため、
  指数バックオフでリトライする。
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Optional

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# 列定義（順序が重要。変更時は HEADER も合わせて更新する）
COL_TODO_ID    = 0
COL_USER_ID    = 1
COL_TITLE      = 2
COL_CONTENT    = 3
COL_DUE_DATE   = 4
COL_STATUS     = 5
COL_CREATED_AT = 6

HEADER = ["todo_id", "user_id", "title", "content", "due_date", "status", "created_at"]

# モジュールレベルのキャッシュ（プロセスが生きている間は再利用）
_client: Optional[gspread.Client] = None
_worksheet: Optional[gspread.Worksheet] = None


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _get_client() -> gspread.Client:
    """gspread クライアントを返す。初回のみ認証し、以降はキャッシュを返す。"""
    global _client
    if _client is not None:
        return _client

    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise EnvironmentError(
            "環境変数 GOOGLE_CREDENTIALS_JSON が設定されていません。"
        )

    try:
        creds_dict = json.loads(creds_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"GOOGLE_CREDENTIALS_JSON が有効な JSON ではありません: {e}") from e

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    _client = gspread.authorize(creds)
    logger.info("Google Sheets クライアントを初期化しました。")
    return _client


def _get_worksheet() -> gspread.Worksheet:
    """
    'todos' ワークシートを返す。
    - 存在しない場合は自動作成してヘッダーを書き込む。
    - ヘッダーが不正な場合は RuntimeError を送出する。
    キャッシュ済みのワークシートが接続切れになった場合は再取得する。
    """
    global _worksheet

    if _worksheet is not None:
        return _worksheet

    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise EnvironmentError("環境変数 SPREADSHEET_ID が設定されていません。")

    client = _get_client()

    try:
        spreadsheet = client.open_by_key(spreadsheet_id)
    except SpreadsheetNotFound:
        raise ValueError(
            f"スプレッドシート ID '{spreadsheet_id}' が見つかりません。"
            " サービスアカウントに編集権限を付与してください。"
        )

    try:
        ws = spreadsheet.worksheet("todos")
        # 既存シートのヘッダーを検証する
        existing_header = ws.row_values(1)
        if existing_header != HEADER:
            raise RuntimeError(
                f"ワークシート 'todos' のヘッダーが想定と異なります。\n"
                f"  期待: {HEADER}\n"
                f"  実際: {existing_header}"
            )
    except WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="todos", rows=1000, cols=len(HEADER))
        ws.append_row(HEADER, value_input_option="RAW")
        logger.info("ワークシート 'todos' を新規作成しました。")

    _worksheet = ws
    return _worksheet


def _retry(fn, retries: int = 3, base_delay: float = 2.0):
    """
    Sheets API のレート制限 (429) や一時的なエラー (5xx) に対して
    指数バックオフでリトライする。
    """
    for attempt in range(retries):
        try:
            return fn()
        except APIError as e:
            status = e.response.status_code
            if status in (429, 500, 502, 503) and attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Sheets API エラー %s。%s 秒後にリトライします（%s/%s）",
                    status, delay, attempt + 1, retries,
                )
                time.sleep(delay)
            else:
                raise
    # ここには到達しないが型チェックのため
    raise RuntimeError("リトライ上限に達しました。")


def _rows_to_dicts(rows: list[list[str]]) -> list[dict]:
    """get_all_values() の生データ（ヘッダー含む）を dict のリストに変換する。"""
    if not rows or rows[0] != HEADER:
        return []
    return [dict(zip(HEADER, row + [""] * (len(HEADER) - len(row)))) for row in rows[1:]]


def _find_row_index(rows: list[list[str]], user_id: str, todo_id: str) -> Optional[int]:
    """
    全行データから対象行の 1-indexed 行番号を返す（ヘッダー行を含む）。
    見つからない場合は None を返す。
    """
    for i, row in enumerate(rows[1:], start=2):  # rows[0] はヘッダー
        if len(row) > max(COL_USER_ID, COL_TODO_ID):
            if row[COL_TODO_ID] == todo_id and row[COL_USER_ID] == user_id:
                return i
    return None


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def get_todos(user_id: str) -> list[dict]:
    """
    指定ユーザーの Todo 一覧を返す。
    並び順: 未完了（期日昇順） → 完了済み（期日昇順）
    """
    ws = _get_worksheet()
    rows = _retry(ws.get_all_values)
    todos = [r for r in _rows_to_dicts(rows) if r["user_id"] == user_id]
    todos.sort(key=lambda t: (t["status"] == "done", t["due_date"] or "9999-99-99"))
    return todos


def add_todo(user_id: str, title: str, content: str, due_date: str) -> dict:
    """新しい Todo を追加してその dict を返す。"""
    ws = _get_worksheet()
    todo_id = str(uuid.uuid4())
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [todo_id, user_id, title, content, due_date, "pending", created_at]
    _retry(lambda: ws.append_row(row, value_input_option="RAW"))
    logger.info("Todo を追加しました: todo_id=%s user_id=%s", todo_id, user_id)
    return dict(zip(HEADER, row))


def get_todo(user_id: str, todo_id: str) -> Optional[dict]:
    """指定の Todo を1件返す。見つからない場合は None。"""
    ws = _get_worksheet()
    rows = _retry(ws.get_all_values)
    idx = _find_row_index(rows, user_id, todo_id)
    if idx is None:
        return None
    row = rows[idx - 1]  # 0-indexed
    return dict(zip(HEADER, row + [""] * (len(HEADER) - len(row))))


def update_todo(
    user_id: str,
    todo_id: str,
    title: str,
    content: str,
    due_date: str,
    status: str,
) -> bool:
    """
    指定の Todo を更新する。
    成功時は True、対象行が見つからない場合は False を返す。
    """
    ws = _get_worksheet()
    rows = _retry(ws.get_all_values)
    idx = _find_row_index(rows, user_id, todo_id)
    if idx is None:
        logger.warning("update_todo: 対象行が見つかりません todo_id=%s", todo_id)
        return False

    original_row = rows[idx - 1]
    created_at = original_row[COL_CREATED_AT] if len(original_row) > COL_CREATED_AT else ""
    new_row = [todo_id, user_id, title, content, due_date, status, created_at]

    cell_range = f"A{idx}:{chr(ord('A') + len(HEADER) - 1)}{idx}"
    _retry(lambda: ws.update(cell_range, [new_row], value_input_option="RAW"))
    logger.info("Todo を更新しました: todo_id=%s status=%s", todo_id, status)
    return True


def delete_todo(user_id: str, todo_id: str) -> bool:
    """
    指定の Todo を削除する。
    成功時は True、対象行が見つからない場合は False を返す。
    """
    ws = _get_worksheet()
    rows = _retry(ws.get_all_values)
    idx = _find_row_index(rows, user_id, todo_id)
    if idx is None:
        logger.warning("delete_todo: 対象行が見つかりません todo_id=%s", todo_id)
        return False

    _retry(lambda: ws.delete_rows(idx))
    logger.info("Todo を削除しました: todo_id=%s", todo_id)
    return True


def reset_cache() -> None:
    """テストやホットリロード時にモジュールキャッシュをクリアする。"""
    global _client, _worksheet
    _client = None
    _worksheet = None
