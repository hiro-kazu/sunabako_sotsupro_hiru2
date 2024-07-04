import os
from dotenv import load_dotenv
from flask import Flask, request, abort
import ssl
import certifi
import sqlite3  # SQLiteライブラリのインポート
import datetime
import schedule
import time
import threading
import traceback

from linebot.v3 import (
    WebhookHandler
)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)


# .envファイルの読み込み
load_dotenv()

# 環境変数の取得
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

# 環境変数が設定されていない場合はエラーを出力
if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise ValueError("CHANNEL_ACCESS_TOKEN or CHANNEL_SECRET is not set in the environment variables")

app = Flask(__name__)

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

# SSL証明書の設定
ssl_context = ssl.create_default_context(cafile=certifi.where())

# SSL証明書の検証を無効にする（開発環境でのみ使用してください）
# ssl._create_default_https_context = ssl._create_unverified_context

# データベースの初期設定
def setup_database():
    conn = sqlite3.connect('budget.db')
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS budgets (
        user_id TEXT PRIMARY KEY,
        budget INTEGER
    )
    ''')

    c.execute('''
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        amount INTEGER,
        date DATE DEFAULT CURRENT_DATE,
        FOREIGN KEY (user_id) REFERENCES budgets (user_id)
    )
    ''')

    conn.commit()
    conn.close()

setup_database()

# 予算情報をデータベースに保存する関数
def register_budget(user_id, budget):
    conn = sqlite3.connect('budget.db')
    c = conn.cursor()
    c.execute('''
    INSERT OR REPLACE INTO budgets (user_id, budget) VALUES (?, ?)
    ''', (user_id, budget))
    conn.commit()
    conn.close()

# 支出情報をデータベースに保存する関数
def register_expense(user_id, amount):
    try:
        conn = sqlite3.connect('budget.db')
        c = conn.cursor()
        c.execute('''
        INSERT INTO expenses (user_id, amount) VALUES (?, ?)
        ''', (user_id, amount))
        conn.commit()
        conn.close()
        print(f"Expense registered: user_id={user_id}, amount={amount}")
        return True
    except Exception as e:
        print(f"Error registering expense: {str(e)}")
        traceback.print_exc()
        return False

# これまでの全支出を取得する関数
def get_total_expenses(user_id):
    conn = sqlite3.connect('budget.db')
    c = conn.cursor()
    c.execute('''
    SELECT SUM(amount) FROM expenses WHERE user_id = ?
    ''', (user_id,))
    total_expenses = c.fetchone()[0]
    conn.close()
    return total_expenses if total_expenses else 0

# 予算から支出を引いた金額を計算して更新する関数
def update_budget(user_id):
    conn = sqlite3.connect('budget.db')
    c = conn.cursor()
    c.execute('SELECT budget FROM budgets WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    if row:
        initial_budget = row[0]
        total_expenses = get_total_expenses(user_id)
        remaining_budget = initial_budget - total_expenses
        c.execute('''
        UPDATE budgets SET budget = ? WHERE user_id = ?
        ''', (remaining_budget, user_id))
        conn.commit()
    conn.close()

# ユーザーの予算を取得する関数
def get_budget(user_id):
    conn = sqlite3.connect('budget.db')
    c = conn.cursor()
    c.execute('SELECT budget FROM budgets WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        initial_budget = row[0]
        total_expenses = get_total_expenses(user_id)
        current_budget = initial_budget - total_expenses
        return initial_budget, current_budget
    else:
        return None, None  # エラー処理やデフォルト値を返す場合は適宜変更してください

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    print("Received message: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    print("Handling message: " + event.message.text)
    message_text = event.message.text.strip()
    user_id = event.source.user_id
    response_message = "申し訳ありませんが、エラーが発生しました。"

    if message_text.lower().startswith("予算登録"):
        try:
            budget = int(message_text.split()[1])
            register_budget(user_id, budget)
            response_message = f"予算が{budget}円に設定されました。\n"
            # 予算設定後に現在の予算を更新
            update_budget(user_id)
            current_budget = get_budget(user_id)
            if current_budget is not None:
                response_message += f"現在の予算は{current_budget}円です。"
            else:
                response_message += "現在、予算が設定されていません。"
        except (IndexError, ValueError):
            response_message = "予算登録コマンドの形式は「予算登録 金額」です。"

    elif message_text.lower().startswith("支出登録"):
        try:
            amount = int(message_text.split()[1])
            if register_expense(user_id, amount):
                response_message = f"{amount}円の支出を登録しました。\n"

                # 支出を登録した後、予算を更新する
                update_budget(user_id)
                current_budget = get_budget(user_id)
                response_message += f"現在の予算は{current_budget}円です。"
            else:
                response_message = "支出の登録に失敗しました。もう一度お試しください。"
        except (IndexError, ValueError):
            response_message = "支出登録コマンドの形式は「支出登録 金額」です。"

    elif message_text.lower() == "予算確認":
        try:
            initial_budget, current_budget = get_budget(user_id)
            if initial_budget is not None and current_budget is not None:
                total_expenses = initial_budget - current_budget
                response_message = f"現在の予算: {initial_budget}円\n"
                response_message += f"総支出: {total_expenses}円\n"
                response_message += f"残りの予算: {current_budget}円"
            else:
                response_message = "現在、予算が設定されていません。"
        except Exception as e:
            response_message = "予算の確認中にエラーが発生しました。"
            print(f"Error checking budget: {str(e)}")
            traceback.print_exc()

    else:
        response_message = "予算を登録するには「予算登録 金額」と、支出を登録するには「支出登録 金額」と入力してください。"

    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message_with_http_info(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=response_message)]
                )
            )
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        traceback.print_exc()

# 定期的に予算を更新する処理を追加
def daily_budget_update():
    conn = sqlite3.connect('budget.db')
    c = conn.cursor()
    
    # 全てのユーザーの予算を取得して更新する
    c.execute('SELECT user_id, budget FROM budgets')
    rows = c.fetchall()
    
    for row in rows:
        user_id, initial_budget = row
        update_budget(user_id, initial_budget)
        print(f"Updated budget for user {user_id}")
    
    conn.close()

# スケジュールを設定し、毎日AM7:00に予算を更新する
schedule.every().day.at("07:10").do(daily_budget_update)

# スケジューラを実行する関数
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    # 予算の初期設定
    setup_database()
    
    # スケジューラを別スレッドで実行
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.start()
    
    # Flaskアプリを実行
    app.run(debug=True, host='0.0.0.0', port=5000)