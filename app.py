import logging
import os
import uuid
from datetime import date, timedelta

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from dotenv import load_dotenv

load_dotenv()

import sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

VALID_STATUSES = {"pending", "done"}


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

        if not title:
            flash("タイトルは必須です。", "error")
            return redirect(url_for("todo_list", user_id=user_id))

        try:
            sheets.add_todo(user_id, title, content, due_date)
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
    urgent_date = (date.today() + timedelta(days=3)).isoformat()
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
            new_status,
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
        status = request.form.get("status", "pending")

        if not title:
            flash("タイトルは必須です。", "error")
            return render_template("edit.html", todo=todo, user_id=user_id, today=date.today().isoformat())

        if status not in VALID_STATUSES:
            flash("不正なステータス値です。", "error")
            return render_template("edit.html", todo=todo, user_id=user_id, today=date.today().isoformat())

        try:
            sheets.update_todo(user_id, todo_id, title, content, due_date, status)
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
        found = sheets.delete_todo(user_id, todo_id)
        if found:
            flash("Todoを削除しました。", "success")
    except Exception:
        logging.exception("Todo の削除に失敗しました")
        flash("削除に失敗しました。しばらく待ってから再度お試しください。", "error")

    return redirect(url_for("todo_list", user_id=user_id))


if __name__ == "__main__":
    app.run(debug=True)
