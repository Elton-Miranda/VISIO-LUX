import os
import logging
import re
import edge_tts
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from groq import Groq
from dotenv import load_dotenv
import prompts

# ==============================================================================
# 1. CONFIGURAÇÕES & ENV
# ==============================================================================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("❌ ERRO: Chaves não encontradas no arquivo .env")

client = Groq(api_key=GROQ_API_KEY)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

VOZ_NEURAL = "pt-BR-AntonioNeural"

# ==============================================================================
# 2. FUNÇÕES AUXILIARES
# ==============================================================================

def limpar_markdown(texto):
    """Remove caracteres especiais para o áudio ficar natural."""
    texto_limpo = re.sub(r'\*+', '', texto) # Tira negrito
    texto_limpo = re.sub(r'\#+', '', texto_limpo) # Tira titulos
    texto_limpo = re.sub(r'_+', '', texto_limpo) # Tira italico
    texto_limpo = re.sub(r'^- ', '', texto_limpo, flags=re.MULTILINE) # Tira hifens de lista
    return texto_limpo

def transcrever_audio(caminho_arquivo):
    try:
        with open(caminho_arquivo, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                file=(caminho_arquivo, audio_file.read()),
                model="whisper-large-v3",
                language="pt",
                response_format="text"
            )
        return transcript
    except Exception as e:
        logging.error(f"Erro Whisper: {e}")
        return None

async def gerar_e_enviar_audio(context, chat_id, texto, user_id):
    """Gera o áudio e envia (usado apenas quando o técnico manda áudio)."""
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
        
        # Limpa o texto e gera o arquivo
        texto_limpo = limpar_markdown(texto)
        arquivo_saida = f"resposta_{user_id}.mp3"
        communicate = edge_tts.Communicate(texto_limpo, VOZ_NEURAL)
        await communicate.save(arquivo_saida)
        
        # Envia
        with open(arquivo_saida, 'rb') as audio:
            await context.bot.send_voice(chat_id=chat_id, voice=audio)
            
    except Exception as e:
        logging.error(f"Erro no envio de áudio: {e}")
    finally:
        # Limpeza do arquivo
        if os.path.exists(arquivo_saida):
            os.remove(arquivo_saida)

def consultar_lumen(texto_usuario):
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {"role": "user", "content": texto_usuario}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.2,
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        logging.error(f"Erro Llama: {e}")
        return "⚠️ Erro no sistema de inteligência."

# ==============================================================================
# 3. HANDLERS (A LÓGICA DE ESPELHAMENTO)
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    msg = (
        f"⚡ **Olá, {user_name}! Sou o Lúmen.**\n\n"
        "**Como eu funciono:**\n"
        "📝 Se você **escrever**, eu respondo em texto (para locais barulhentos).\n"
        "🗣️ Se você mandar **áudio**, eu respondo em áudio e texto.\n\n"
        "Pode mandar seu cenário!"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    CENÁRIO 1: Técnico escreve.
    AÇÃO: Bot responde APENAS TEXTO (Ideal para ruído/sigilo).
    """
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    # 1. Processa a resposta
    resposta = consultar_lumen(update.message.text)
    
    # 2. Envia APENAS texto
    await context.bot.send_message(chat_id=update.effective_chat.id, text=resposta)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    CENÁRIO 2: Técnico manda áudio.
    AÇÃO: Bot responde TEXTO + ÁUDIO (Ideal para mãos ocupadas).
    """
    await context.bot.send_message(chat_id=update.effective_chat.id, text="🎧 _Ouvindo..._", parse_mode='Markdown')
    
    file_info = await context.bot.get_file(update.message.voice.file_id)
    caminho = f"audio_{update.message.from_user.id}.ogg"
    await file_info.download_to_drive(caminho)

    try:
        texto_usuario = transcrever_audio(caminho)
        
        if texto_usuario:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"📝 *Entendi:* \"{texto_usuario}\"", parse_mode='Markdown')
            
            # 1. Processa a resposta
            resposta = consultar_lumen(texto_usuario)
            
            # 2. Envia TEXTO (Referência visual)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=resposta)
            
            # 3. Envia ÁUDIO (Explicação falada)
            await gerar_e_enviar_audio(context, update.effective_chat.id, resposta, update.effective_user.id)
            
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Não consegui entender o áudio.")
            
    finally:
        if os.path.exists(caminho):
            os.remove(caminho)

# ==============================================================================
# 4. START
# ==============================================================================
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("✅ VISIO LUX ESTÁ ON!")
    application.run_polling()