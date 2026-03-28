import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

TEMP_DIR = "temp"
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# TTS chunk size in characters (edge-tts can time out on very long strings)
TTS_CHUNK_SIZE = 4000

# Timeout per TTS chunk in seconds (edge-tts can hang on some text)
TTS_CHUNK_TIMEOUT = 120

# Number of retries per chunk before giving up
TTS_CHUNK_RETRIES = 3

# Pages with fewer characters than this are considered blank/irrelevant
MIN_PAGE_CHARS = 30

# Fallback page-chunk size when no chapters are detected
FALLBACK_PAGE_CHUNK = 20

# Header/footer frequency threshold: text appearing on more than this
# fraction of pages in extreme Y positions is treated as header/footer.
HEADER_FOOTER_FREQ_THRESHOLD = 0.40

# Maximum file size the bot will accept (Telegram bot API download limit)
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

# Telegram bot API upload limit for sendAudio
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB

LANGUAGES = {
    "Ukrainian": ["uk-UA-OstapNeural", "uk-UA-PolinaNeural"],
    "Polish": ["pl-PL-MarekNeural", "pl-PL-ZofiaNeural"],
    "English": ["en-CA-LiamNeural", "en-US-RogerNeural", "en-GB-SoniaNeural"],
    "Italian": ["it-IT-DiegoNeural", "it-IT-ElsaNeural", "it-IT-IsabellaNeural"],
}

# Localized greetings for voice preview
GREETINGS = {
    "uk": "Вітаю, я {name}. Я прочитаю вашу книгу для вас!",
    "it": "Ciao, sono {name}. Leggerò il tuo libro per te!",
    "en": "Hello, I am {name}. I will read your book for you!",
    "pl": "Cześć, jestem {name}. Przeczytam dla Ciebie twoją książkę!",
}
