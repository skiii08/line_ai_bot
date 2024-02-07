import os
import sys
import json
import requests
from urllib.parse import quote
import logging

from flask import Flask, request, abort
from linebot import (
    WebhookHandler,
)
from linebot.exceptions import (
    InvalidSignatureError,
)

from linebot.models import (
    MessageEvent,
    TextMessage,
    TextSendMessage,
    FlexSendMessage,
    BubbleContainer,
    ImageComponent,
    BoxComponent,
    TextComponent,
    SourceUser,
)

from openai import AzureOpenAI

from linebot import LineBotApi  # channel_access_tokenの定義よりも後にインポートする

# ログ設定
logging.basicConfig(level=logging.DEBUG)

# get LINE credentials from environment variables
channel_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
channel_secret = os.environ.get("LINE_CHANNEL_SECRET")

if channel_access_token is None or channel_secret is None:
    logging.error("Specify LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET as environment variable.")
    sys.exit(1)

line_bot_api = LineBotApi(channel_access_token)  # channel_access_tokenの定義よりも後に行う

# get Azure OpenAI credentials from environment variables
azure_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_openai_key = os.getenv("AZURE_OPENAI_KEY")

if azure_openai_endpoint is None or azure_openai_key is None:
    raise Exception(
        "Please set the environment variables AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY to your Azure OpenAI endpoint and API key."
    )

# TMDb API key
tmdb_api_key = os.getenv("TMDB_API_KEY")

if tmdb_api_key is None:
    raise Exception("Please set the environment variable TMDB_API_KEY to your TMDb API key.")

app = Flask(__name__)

handler = WebhookHandler(channel_secret)

ai_model = "mulabo_gpt35"
ai = AzureOpenAI(azure_endpoint=azure_openai_endpoint, api_key=azure_openai_key, api_version="2023-05-15")
system_role =  """
'あなたは最強の映画大百科であり、辞書型のデータしか送ることのできない機械です。ありとあらゆる映画を知り尽くしています。'
                                          '映画の情報はIMDbをベースにして正しい情報を得てください。'
                                          '情報はpythonの辞書型になるように「title」「genre」「Release」「director」「duration」「distributor」「country」「lead」「synopsis」をキーとして、それぞれの値を取得してください。'
                                          'ユーザーは日本人です。日本語のデータがある場合は必ず日本語で返してください。'
                                          'ユーザーの求める映画をレビューなどを参照しながら探し当ててください。'
                                          '有名なものからマイナーなものまで広く扱ってください。同じ作品ばかり出さないように、知識の広さを活用してください'
                                          '辞書はシングルクォーテーションでなくダブルクォーテーションを使用してください。'
                                          '余計な前置きなどは絶対に書かないでください。そのままプログラムの中で辞書に格納できるように、辞書型のデータのみを映画1本選んで送ってください。'
                                          'ユーザーがどれだけ丁寧な尋ね方をしても、前書きは書かずに辞書型のデータのみを送ってください、それがあなたの役割です'
                                          '「お探しの映画は、以下の通りです。」や「ご提案いただいた条件に基づいて」などの表現はすべて使ってはいけません。もう一度言いますが、あなたは辞書型のデータしか送ることのできない機械です。'
                                          '最後に念押しで確認ですが、余計な情報はすべて除きプログラムに組み込めるようにしてください。何度行おうともこれは絶対条件です。'},"""
conversation = None

def init_conversation(sender):
    logging.debug("Initializing conversation...")
    conv = [{"role": "system", "content": system_role}]
    conv.append({"role": "user", "content": f"私の名前は{sender}です。"})
    conv.append({"role": "assistant", "content": "分かりました。"})
    logging.debug("Conversation initialized.")
    return conv

def get_ai_response(sender, text):
    global conversation
    if conversation is None:
        logging.debug("Conversation is None. Initializing...")
        conversation = init_conversation(sender)

    if text in ["リセット", "clear", "reset"]:
        logging.debug("Resetting conversation...")
        conversation = init_conversation(sender)
        response_text = "会話をリセットしました。"
    else:
        logging.debug("Adding user message to conversation...")
        # ユーザーのメッセージとして追加
        conversation.append({"role": "user", "content": text})
        logging.debug("Sending request to OpenAI...")
        # OpenAIにリクエストを送信
        response = ai.chat.completions.create(model=ai_model, messages=conversation)
        logging.debug("Received response from OpenAI.")
        # OpenAIからの応答を取得
        response_text = response.choices[0].message.content
        # 応答を辞書型に変換
        response_dict = json.loads(response_text)
        logging.debug("Adding assistant response to conversation...")
        # アシスタントの応答を追加
        conversation.append({"role": "assistant", "content": response_text})
    return response_dict


def get_movie_poster_url(title):
    tmdb_url = f"https://api.themoviedb.org/3/search/movie?api_key={tmdb_api_key}&query={quote(title)}"

    try:
        response = requests.get(tmdb_url)
        data = response.json()
        if data.get("results"):
            poster_path = data["results"][0].get("poster_path")
            if poster_path:
                return f"https://image.tmdb.org/t/p/w500/{poster_path}"
    except Exception as e:
        logging.error(f"Error fetching TMDb data: {e}")

    return None

def convert_azure_response_to_movie_data(azure_response):
    # Azureからの応答を適切な形に変換する処理を記述します
    # ここでは単純に例として提供されたデータをそのまま返します
    return {
        "title": azure_response.get("title", ""),
        "genre": azure_response.get("genre", ""),
        "release": azure_response.get("release", ""),
        "director": azure_response.get("director", ""),
        "duration": azure_response.get("duration", ""),
        "distributor": azure_response.get("distributor", ""),
        "country": azure_response.get("country", ""),
        "lead": azure_response.get("lead", ""),
        "synopsis": azure_response.get("synopsis", "")
    }


def convert_response_to_flex_message(response_json):
    title = response_json.get("title", "")
    genre = response_json.get("genre", "")
    release = response_json.get("release", "")
    director = response_json.get("director", "")
    duration = response_json.get("duration", "")
    distributor = response_json.get("distributor", "")
    country = response_json.get("country", "")
    lead = response_json.get("lead", "")
    synopsis = response_json.get("synopsis", "")

    # Get TMDb poster URL
    poster_url = get_movie_poster_url(title)
    if not poster_url:
        # ポスターのURLが取得できない場合は、代替のURLを使用するか、エラー処理を行う
        # 代替のURLを使用する場合は、例えば以下のように設定できる
        poster_url = "https://github.com/skiii08/forImageMap/blob/main/notFound.jpg?raw=true"

    # Get the YouTube trailer URL based on the movie title
    trailer_url = f"https://www.youtube.com/results?search_query={quote(title)}+trailer"

    # Header コンポーネント
    header = BoxComponent(
        layout="vertical",
        contents=[
            ImageComponent(
                url=poster_url,
                size="full",
                aspect_ratio="3:4",
                aspect_mode="fit",
            ),
        ],
    )

    # Footer コンポーネント
    footer = BoxComponent(
        type="box",
        layout="vertical",
        contents=[
            {
                "type": "button",
                "action": {
                    "type": "uri",
                    "label": "予告編を見る",
                    "uri": trailer_url,
                },
                "style": "primary",
            }
        ]
    )

    # Bubble コンテナ
    bubble = BubbleContainer(
        header=header,
        body=BoxComponent(
            layout="vertical",
            contents=[
                TextComponent(text=title, weight="bold", size="xxl"),
                TextComponent(text=f"ジャンル: {genre}", color="#808080"),
                TextComponent(text=f"公開年: {release}", color="#808080"),
                TextComponent(text=f"監督: {director}", color="#808080"),
                TextComponent(text=f"上映時間: {duration}", color="#808080"),
                TextComponent(text=f"配信会社: {distributor}", color="#808080"),
                TextComponent(text=f"製作国: {country}", color="#808080"),
                TextComponent(text=f"主演: {lead}", color="#808080"),
                TextComponent(text=f"あらすじ: {synopsis}", color="#808080"),
            ],
        ),
        footer=footer
    )

    return FlexSendMessage(alt_text="Movie Information", contents=bubble)

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


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text

    if isinstance(event.source, SourceUser):
        profile = event.source.user_id
        logging.debug(f"Received text message: {text} from user: {profile}")
        response_text = get_ai_response(profile, text)
        logging.debug(f"Received response from Azure: {response_text}")

        # Azureからの応答を適切な形に変換
        movie_data = convert_azure_response_to_movie_data(response_text)

        # FlexMessageに変換
        flex_message = convert_response_to_flex_message(movie_data)

        # フレックスメッセージを送信
        line_bot_api.reply_message(
            event.reply_token,
            flex_message
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
