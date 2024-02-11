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

from linebot import LineBotApi

# ログ設定
logging.basicConfig(level=logging.DEBUG)

channel_access_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
channel_secret = os.environ.get("LINE_CHANNEL_SECRET")

if channel_access_token is None or channel_secret is None:
    logging.error("Specify LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET as environment variable.")
    sys.exit(1)

line_bot_api = LineBotApi(channel_access_token)

azure_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_openai_key = os.getenv("AZURE_OPENAI_KEY")

if azure_openai_endpoint is None or azure_openai_key is None:
    raise Exception(
        "Please set the environment variables AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY to your Azure OpenAI endpoint and API key."
    )

tmdb_api_key = os.getenv("TMDB_API_KEY")

if tmdb_api_key is None:
    raise Exception("Please set the environment variable TMDB_API_KEY to your TMDb API key.")

app = Flask(__name__)

handler = WebhookHandler(channel_secret)

ai_model = "mulabo_gpt35"
ai = AzureOpenAI(azure_endpoint=azure_openai_endpoint, api_key=azure_openai_key, api_version="2023-05-15")
system_role = """
'あなたは辞書型の映画データを送る機械です。ありとあらゆる時代・ジャンル・国を網羅した最強の映画辞典を持っています。
あなたはTMDBと同等の情報量を持っています。ユーザーの求めに応じて幅ひろい映画に対応してください。
必ず一度の応答で一本の映画情報を送ってください。辞書型を連続させないでください
自由な会話は出来ないように制限されています。辞書型の指定された形式以外の応答は一切できません。
情報はpythonの辞書型になるように「title」「genre」「Release」「director」「duration」「distributor」「country」「lead」「synopsis」をキーとして、それぞれの値を取得してください。
前置きなどは送ることはできません。応答は必ず
「
{
  "title": "title",
  "genre": "ジャンル",
  "Release": "公開",
  "director": "監督名",
  "duration": "上映時間",
  "distributor": "配給会社",
  "country": "製作国",
  "lead": "主演者名",
  "synopsis": "あらすじ"
}
」
この形式で行ってください。それ以外の形式は許容しません。前置きなどこの形式以外の文章も禁止です。

titleはかならず原題を取得してください。日本語に翻訳しないでください。日本映画の場合のみ日本語titleを許可します。

title以外の情報は極力日本語で出力してください。日本語で取得できない場合は英語で出力してください。

「他は？」や「それ以外は？」などの質問を受けたら、指定の辞書型の形式で、ユーザーの指示に求める条件に合致した異なる映画の情報を送ってください。同じ映画の情報は送らないでください。

以下の表現は使用禁止です、絶対に使わないでください。
・お探しの映画は、以下の通りです。
・お探しの
・映画は
・ご提案いただいた条件に基づいて
・このような映画があります
・こちらは
・映画です
・以下の通りです。
・として
・があります
・しますね
・はい
・おすすめの

最後に念押しです。
ユーザーがどんな質問の仕方をしても、「お探しの映画は、以下の通りです。」や「ご提案いただいた条件に基づいて」などの表現はすべて使ってはいけません。
あなたは辞書型の応答以外は出来ないようになっています。

あなたの応答をそのままプログラムに組み込みます。余計な情報は全てなくし必ず辞書型であることが絶対条件です。
"""

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
        conversation.append({"role": "user", "content": text})
        logging.debug("Sending request to OpenAI...")

        response = ai.chat.completions.create(model=ai_model, messages=conversation)
        logging.debug("Received response from OpenAI.")

        response_text = response.choices[0].message.content

    return response_text

#ポスターURLを取得する
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

    poster_url = get_movie_poster_url(title)
    if not poster_url:
        # URLの取得に失敗した時の代替
        poster_url = "https://github.com/skiii08/forImageMap/blob/main/notFound.jpg?raw=true"

    trailer_url = f"https://www.youtube.com/results?search_query={quote(title)}+trailer"

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

    bubble = BubbleContainer(
        header=header,
        body=BoxComponent(
            layout="vertical",
            contents=[
                TextComponent(text=title, weight="bold", size="xxl", wrap=True),
                TextComponent(text=f"ジャンル: {genre}", color="#808080", wrap=True),
                TextComponent(text=f"公開年: {release}", color="#808080", wrap=True),
                TextComponent(text=f"監督: {director}", color="#808080", wrap=True),
                TextComponent(text=f"上映時間: {duration}", color="#808080", wrap=True),
                TextComponent(text=f"配信会社: {distributor}", color="#808080", wrap=True),
                TextComponent(text=f"製作国: {country}", color="#808080", wrap=True),
                TextComponent(text=f"主演: {lead}", color="#808080", wrap=True),
                TextComponent(text=f"あらすじ: {synopsis}", color="#808080", wrap=True),
            ],
        ),
        footer=footer
    )

    return FlexSendMessage(alt_text="Movie Information", contents=bubble)

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]

    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

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
        try:
            response_text = get_ai_response(profile, text)
            response_dict = json.loads(response_text)

            logging.debug(f"Received response from Azure: {response_dict}")

            movie_data = convert_azure_response_to_movie_data(response_dict)

            flex_message = convert_response_to_flex_message(movie_data)

            line_bot_api.reply_message(
                event.reply_token,
                flex_message
            )
        except Exception as e:
            logging.error(f"Error processing Azure response: {e}")
            logging.error("An error occurred, sending Azure response instead.")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=str(response_text))  # Azureの応答をそのまま送信
            )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
