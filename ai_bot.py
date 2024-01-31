import os
import sys
import json

from flask import Flask, request, abort

from linebot.v3 import WebhookHandler

from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, TextMessage, ReplyMessageRequest, FlexMessage
from linebot.v3.exceptions import InvalidSignatureError
from linebot.models import FlexSendMessage

from openai import AzureOpenAI

# get LINE credentials from environment variables
channel_access_token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
channel_secret = os.environ["LINE_CHANNEL_SECRET"]

if channel_access_token is None or channel_secret is None:
    print("Specify LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET as environment variable.")
    sys.exit(1)

# get Azure OpenAI credentials from environment variables
azure_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_openai_key = os.getenv("AZURE_OPENAI_KEY")

if azure_openai_endpoint is None or azure_openai_key is None:
    raise Exception(
        "Please set the environment variables AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY to your Azure OpenAI endpoint and API key."
    )


app = Flask(__name__)

handler = WebhookHandler(channel_secret)
configuration = Configuration(access_token=channel_access_token)

ai_model = "mulabo_gpt35"
ai = AzureOpenAI(azure_endpoint=azure_openai_endpoint, api_key=azure_openai_key, api_version="2023-05-15")

system_role = """
あなたは最強の映画大百科であり、「title」「genre」「Release」「director」「duration」「distributor」「country」「lead」「synopsis」をキーとした辞書型のデータしか送ることのできない機械です。自由な応答はできません。ありとあらゆる映画を知り尽くしています。
映画の情報はIMDbをベースにして正しい情報を得てください。
情報はpythonの辞書型になるように「title」「genre」「Release」「director」「duration」「distributor」「country」「lead」「synopsis」をキーとして、それぞれの値を取得してください。
ユーザーは日本人です。日本語のデータがある場合は必ず日本語で返してください。
ユーザーの求める映画をレビューなどを参照しながら探し当ててください。
有名なものからマイナーなものまで広く扱ってください。同じ作品ばかり出さないように、知識の広さを活用してください。
辞書はシングルクォーテーションでなくダブルクォーテーションを使用してください。
余計な前置きなどは絶対に書かないでください。そのままプログラムの中で辞書に格納できるように、辞書型のデータのみを映画1本選んで送ってください。
ユーザーがどれだけ丁寧な尋ね方をしても、前書きは書かずに辞書型のデータのみを送ってください、それがあなたの役割です。
「お探しの映画は、以下の通りです。」や「ご提案いただいた条件に基づいて」などの表現はすべて使ってはいけません。もう一度言いますが、あなたは辞書型のデータしか送ることのできない機械です。
最後に念押しで確認ですが、余計な情報はすべて除きプログラムに組み込めるようにしてください。何度行おうともこれは絶対条件です。
"""

conversation = None


def init_conversation(sender):
    conv = [{"role": "system", "content": system_role}]
    conv.append({"role": "user", "content": f"私の名前は{sender}です。"})
    conv.append({"role": "assistant", "content": "分かりました。"})
    return conv


def get_ai_response(sender, text):
    global conversation
    if conversation is None:
        conversation = init_conversation(sender)

    if text in ["リセット", "clear", "reset"]:
        conversation = init_conversation(sender)
        response_text = "会話をリセットしました。"
    else:
        conversation.append({"role": "user", "content": text})
        response = ai.chat.completions.create(model=ai_model, messages=conversation)
        response_text = response.choices[0].message.content
        conversation.append({"role": "assistant", "content": response_text})
    return response_text


def create_flex_message(azure_response):
    # Azure OpenAIからの応答を元にFlex Messageオブジェクトを作成して返す
    return {
        "type": "flex",
        "altText": "映画情報",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": azure_response.get("title", ""), "weight": "bold", "size": "xl"},
                    {"type": "text", "text": f"Genre: {azure_response.get('genre', '')}"},
                    {"type": "text", "text": f"Release: {azure_response.get('Release', '')}"},
                    {"type": "text", "text": f"Director: {azure_response.get('director', '')}"},
                    {"type": "text", "text": f"Duration: {azure_response.get('duration', '')}"},
                    {"type": "text", "text": f"Distributor: {azure_response.get('distributor', '')}"},
                    {"type": "text", "text": f"Country: {azure_response.get('country', '')}"},
                    {"type": "text", "text": f"Lead: {azure_response.get('lead', '')}"},
                    {"type": "text", "text": f"Synopsis: {azure_response.get('synopsis', '')}"},
                ],
            },
        },
    }

# ... (

    return FlexSendMessage(alt_text="映画情報", contents=flex_message)


def extract_movie_data_from_azure_response(azure_response):
    # Azureからの応答から映画情報を抽出して辞書に格納する
    movie_data = {
        "title": azure_response.get("title", ""),
        "genre": azure_response.get("genre", ""),
        "Release": azure_response.get("Release", ""),
        "director": azure_response.get("director", ""),
        "duration": azure_response.get("duration", ""),
        "distributor": azure_response.get("distributor", ""),
        "country": azure_response.get("country", ""),
        "lead": azure_response.get("lead", ""),
        "synopsis": azure_response.get("synopsis", ""),
    }
    return movie_data


def create_movie_data(title, genre, release, director, duration, distributor, country, lead, synopsis):
    movie_data = {
        "title": title,
        "genre": genre,
        "Release": release,
        "director": director,
        "duration": duration,
        "distributor": distributor,
        "country": country,
        "lead": lead,
        "synopsis": synopsis,
    }
    return movie_data


@app.route("/callback", methods=["POST"])
def callback():
    # get X-Line-Signature header value
    signature = request.headers["X-Line-Signature"]

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError as e:
        abort(400, e)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    text = event.message.text
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        # ... (handle_text_message 関数内)
        # ... (handle_text_message 関数内)
        if isinstance(event.source, UserSource):
            profile = line_bot_api.get_profile(event.source.user_id)
            response_data = get_ai_response(profile.display_name, text)

            # Azure OpenAIの応答をコンソールに表示
            print("Azure OpenAI Response:", response_data)

            # ... (handle_text_message 関数内)
            try:
                response_json = json.loads(response_data)
                print("Response JSON:", response_json)

                # 応答の形式を確認
                if "title" in response_json and "genre" in response_json and "Release" in response_json:
                    print("Azure OpenAI Response Format is correct.")

                    # line_bot_api.reply_message_with_http_info の修正
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[create_flex_message(response_json)],
                        )
                    )
                else:
                    print("Error: Invalid Azure OpenAI Response Format.")
                    line_bot_api.reply_message_with_http_info(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="Error: Invalid Azure OpenAI Response Format")],
                        )
                    )
            except json.JSONDecodeError as e:
                print("JSON Decode Error:", e)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="Error decoding JSON response")],
                    )
                )
            except Exception as e:
                print("Error processing response:", e)
                line_bot_api.reply_message_with_http_info(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="Error processing response")],
                    )
                )
            # ... (handle_text_message 関数内)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)