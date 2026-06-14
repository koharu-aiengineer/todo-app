import csv
import io
import logging
import os
import uuid
from datetime import date, timedelta

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from dotenv import load_dotenv

load_dotenv()

import sheets
import calendar_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

VALID_STATUSES    = {"pending", "done"}
VALID_PRIORITIES  = {"◎", "○", "△"}
VALID_CATEGORIES  = {"visit", "contract", "other"}


# ---------------------------------------------------------------------------
# エラーハンドラ
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", message="ページが見つかりません。"), 404


@app.errorhandler(Exception)
def handle_exception(e):
    logging.exception("予期しないエラーが発生しました")
    return render_template("error.html", message="サーバーエラーが発生しました。しばらく待ってから再度お試しください。"), 500


# ---------------------------------------------------------------------------
# ルーティング
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """新しい UUID を生成してユーザー専用ページへリダイレクト。"""
    user_id = str(uuid.uuid4())
    return redirect(url_for("todo_list", user_id=user_id))


@app.route("/todo/<user_id>", methods=["GET", "POST"])
def todo_list(user_id):
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        due_date = request.form.get("due_date", "").strip()
        due_time = request.form.get("due_time", "").strip()
        due_end_time = request.form.get("due_end_time", "").strip()
        priority = request.form.get("priority", "○").strip()
        if priority not in VALID_PRIORITIES:
            priority = "○"
        category = request.form.get("category", "other").strip()
        if category not in VALID_CATEGORIES:
            category = "other"

        if not title:
            flash("タイトルは必須です。", "error")
            return redirect(url_for("todo_list", user_id=user_id))

        try:
            event_id = ""
            if due_date:
                event_id = calendar_api.add_event(title, due_date, due_time, due_end_time, content) or ""
            sheets.add_todo(user_id, title, content, due_date, due_time, due_end_time, priority, event_id, category)
            flash(f"「{title}」を追加しました。", "success")
        except Exception:
            logging.exception("Todo の追加に失敗しました")
            flash("追加に失敗しました。しばらく待ってから再度お試しください。", "error")

        return redirect(url_for("todo_list", user_id=user_id))

    try:
        todos = sheets.get_todos(user_id)
    except Exception:
        logging.exception("Todo の取得に失敗しました")
        flash("データの取得に失敗しました。しばらく待ってから再度お試しください。", "error")
        todos = []

    today       = date.today().isoformat()
    urgent_date = (date.today() + timedelta(days=1)).isoformat()
    return render_template("index.html", todos=todos, user_id=user_id,
                           today=today, urgent_date=urgent_date)


@app.route("/todo/<user_id>/complete/<todo_id>", methods=["POST"])
def todo_complete(user_id, todo_id):
    """一覧画面からワンクリックで完了状態に切り替える。"""
    try:
        todo = sheets.get_todo(user_id, todo_id)
        if todo is None:
            abort(404)
        new_status = "pending" if todo["status"] == "done" else "done"
        sheets.update_todo(
            user_id, todo_id,
            todo["title"], todo["content"], todo["due_date"],
            todo.get("due_time", ""),
            todo.get("due_end_time", ""),
            todo.get("priority", "○"),
            todo.get("calendar_event_id", ""),
            new_status,
            todo.get("category", "other"),
        )
    except Exception:
        logging.exception("ステータスの更新に失敗しました")
        flash("更新に失敗しました。", "error")

    return redirect(url_for("todo_list", user_id=user_id))


@app.route("/todo/<user_id>/edit/<todo_id>", methods=["GET", "POST"])
def todo_edit(user_id, todo_id):
    try:
        todo = sheets.get_todo(user_id, todo_id)
    except Exception:
        logging.exception("Todo の取得に失敗しました")
        flash("データの取得に失敗しました。", "error")
        return redirect(url_for("todo_list", user_id=user_id))

    if todo is None:
        abort(404)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        due_date = request.form.get("due_date", "").strip()
        due_time = request.form.get("due_time", "").strip()
        due_end_time = request.form.get("due_end_time", "").strip()
        priority = request.form.get("priority", "○").strip()
        status = request.form.get("status", "pending")
        category = request.form.get("category", "other").strip()

        if priority not in VALID_PRIORITIES:
            priority = "○"
        if category not in VALID_CATEGORIES:
            category = "other"

        if not title:
            flash("タイトルは必須です。", "error")
            return render_template("edit.html", todo=todo, user_id=user_id, today=date.today().isoformat())

        if status not in VALID_STATUSES:
            flash("不正なステータス値です。", "error")
            return render_template("edit.html", todo=todo, user_id=user_id, today=date.today().isoformat())

        try:
            old_event_id = todo.get("calendar_event_id", "")
            new_event_id = old_event_id

            if due_date:
                if old_event_id:
                    calendar_api.update_event(old_event_id, title, due_date, due_time, due_end_time, content)
                else:
                    new_event_id = calendar_api.add_event(title, due_date, due_time, due_end_time, content) or ""
            else:
                if old_event_id:
                    calendar_api.delete_event(old_event_id)
                    new_event_id = ""

            sheets.update_todo(user_id, todo_id, title, content, due_date, due_time, due_end_time, priority, new_event_id, status, category)
            flash(f"「{title}」を更新しました。", "success")
        except Exception:
            logging.exception("Todo の更新に失敗しました")
            flash("更新に失敗しました。しばらく待ってから再度お試しください。", "error")

        return redirect(url_for("todo_list", user_id=user_id))

    today = date.today().isoformat()
    return render_template("edit.html", todo=todo, user_id=user_id, today=today)


@app.route("/todo/<user_id>/delete/<todo_id>", methods=["POST"])
def todo_delete(user_id, todo_id):
    try:
        todo = sheets.get_todo(user_id, todo_id)
        if todo:
            event_id = todo.get("calendar_event_id", "")
            if event_id:
                calendar_api.delete_event(event_id)
        found = sheets.delete_todo(user_id, todo_id)
        if found:
            flash("Todoを削除しました。", "success")
    except Exception:
        logging.exception("Todo の削除に失敗しました")
        flash("削除に失敗しました。しばらく待ってから再度お試しください。", "error")

    return redirect(url_for("todo_list", user_id=user_id))


@app.route("/todo/<user_id>/events")
def todo_events(user_id):
    """FullCalendar 用イベント JSON を返す。"""
    try:
        todos = sheets.get_todos(user_id)
    except Exception:
        logging.exception("イベント取得に失敗しました")
        return jsonify([])

    PRIORITY_COLORS = {"◎": "#ef4444", "○": "#f59e0b", "△": "#10b981"}
    events = []
    for todo in todos:
        if not todo.get("due_date"):
            continue

        color = PRIORITY_COLORS.get(todo.get("priority", "medium"), "#6366f1")
        if todo.get("status") == "done":
            color = "#9ca3af"

        if todo.get("due_time"):
            start = f"{todo['due_date']}T{todo['due_time']}"
            end = f"{todo['due_date']}T{todo['due_end_time']}" if todo.get("due_end_time") else None
        else:
            start = todo["due_date"]
            end = None

        event = {
            "id": todo["todo_id"],
            "title": todo["title"],
            "start": start,
            "color": color,
            "url": url_for("todo_edit", user_id=user_id, todo_id=todo["todo_id"]),
            "extendedProps": {"status": todo.get("status", "pending")},
        }
        if end:
            event["end"] = end
        events.append(event)

    return jsonify(events)


@app.route("/todo/<user_id>/csv")
def todo_csv(user_id):
    """指定期間の Todo を CSV でダウンロードする。"""
    today = date.today()

    start_str = request.args.get("start", "")
    end_str   = request.args.get("end", "")

    if start_str and end_str:
        try:
            start = date.fromisoformat(start_str)
            end   = date.fromisoformat(end_str)
        except ValueError:
            flash("日付の形式が不正です。", "error")
            return redirect(url_for("todo_list", user_id=user_id))
        if start > end:
            start, end = end, start
    else:
        # フォールバック：今週
        start = today - timedelta(days=today.weekday())
        end   = start + timedelta(days=6)

    try:
        todos = sheets.get_todos(user_id)
    except Exception:
        logging.exception("CSV 用 Todo 取得に失敗しました")
        todos = []

    filtered = [
        t for t in todos
        if t.get("due_date") and start.isoformat() <= t["due_date"] <= end.isoformat()
    ]

    CATEGORY_LABEL = {"visit": "訪問", "contract": "契約", "other": "その他"}
    STATUS_LABEL   = {"pending": "未完了", "done": "完了"}

    visit_count    = sum(1 for t in filtered if t.get("category") == "visit")
    contract_count = sum(1 for t in filtered if t.get("category") == "contract")
    other_count    = sum(1 for t in filtered if t.get("category") == "other")

    output = io.StringIO()
    writer = csv.writer(output)

    # サマリーセクション
    writer.writerow(["集計期間", f"{start.isoformat()} 〜 {end.isoformat()}"])
    writer.writerow(["合計", f"{len(filtered)}件"])
    writer.writerow(["訪問", f"{visit_count}件"])
    writer.writerow(["契約", f"{contract_count}件"])
    writer.writerow(["その他", f"{other_count}件"])
    writer.writerow([])  # 空行

    # データ本体
    writer.writerow(["タイトル", "カテゴリ", "見込み", "期日", "開始時刻", "終了時刻", "ステータス", "内容", "作成日時"])
    for t in filtered:
        writer.writerow([
            t.get("title", ""),
            CATEGORY_LABEL.get(t.get("category", ""), t.get("category", "")),
            t.get("priority", ""),
            t.get("due_date", ""),
            t.get("due_time", ""),
            t.get("due_end_time", ""),
            STATUS_LABEL.get(t.get("status", ""), t.get("status", "")),
            t.get("content", ""),
            t.get("created_at", ""),
        ])

    filename = f"todo_{start.isoformat()}_{end.isoformat()}.csv"
    return Response(
        "﻿" + output.getvalue(),  # BOM付きUTF-8（Excelで文字化けしない）
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
