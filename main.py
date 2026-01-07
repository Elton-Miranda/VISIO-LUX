import os
import logging
import re
import edge_tts
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler
)
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

# DEFININDO OS ESTADOS DA CONVERSA (PASSOS DO FORMULÁRIO)
TOPOLOGIA, SINAL, LOCAL, DESCRICAO = range(4)

# ==============================================================================
# 2. FUNÇÕES AUXILIARES
# ==============================================================================

def limpar_markdown(texto):
    """Remove caracteres especiais para o áudio ficar natural."""
    texto_limpo = re.sub(r'\*+', '', texto)
    texto_limpo = re.sub(r'\#+', '', texto_limpo)
    texto_limpo = re.sub(r'_+', '', texto_limpo)
    texto_limpo = re.sub(r'^- ', '', texto_limpo, flags=re.MULTILINE)
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
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
        texto_limpo = limpar_markdown(texto)
        arquivo_saida = f"resposta_{user_id}.mp3"
        communicate = edge_tts.Communicate(texto_limpo, VOZ_NEURAL)
        await communicate.save(arquivo_saida)
        
        with open(arquivo_saida, 'rb') as audio:
            await context.bot.send_voice(chat_id=chat_id, voice=audio)
    except Exception as e:
        logging.error(f"Erro no envio de áudio: {e}")
    finally:
        if os.path.exists(arquivo_saida):
            os.remove(arquivo_saida)

def consultar_lumen(texto_completo):
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {"role": "user", "content": texto_completo}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.2,
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        logging.error(f"Erro Llama: {e}")
        return "⚠️ Erro no sistema de inteligência."

# ==============================================================================
# 3. FLUXO DE CONVERSA (PASSO A PASSO)
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia o diagnóstico."""
    user = update.effective_user.first_name
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"👷 **Olá, {user}! Vamos iniciar o diagnóstico.**\n\nVou te fazer 4 perguntas rápidas para entender o problema.\n\n📍 **1. Qual é a Topologia da Rede?**",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([['Barramento', 'Balanceada (Splitter)']], one_time_keyboard=True)
    )
    return TOPOLOGIA

async def receber_topologia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda a topologia e pergunta o sinal."""
    context.user_data['topologia'] = update.message.text
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📶 **2. Qual nível de sinal (dBm) você mediu?**\n(Digite apenas o número, ex: -28)",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove() # Remove os botões anteriores
    )
    return SINAL

async def receber_sinal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda o sinal e pergunta o local."""
    context.user_data['sinal'] = update.message.text
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🏠 **3. Onde você está medindo?**",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([['CTO', 'Roseta/PTO', 'ONU', 'Cabo Drop']], one_time_keyboard=True)
    )
    return LOCAL

async def receber_local(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda o local e pede a descrição final."""
    context.user_data['local'] = update.message.text
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📝 **4. Descreva o problema ou mande um ÁUDIO explicando o cenário.**",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return DESCRICAO

async def finalizar_diagnostico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Coleta tudo, manda pra IA e encerra."""
    chat_id = update.effective_chat.id
    
    # Verifica se o último passo foi áudio ou texto
    if update.message.voice:
        await context.bot.send_message(chat_id=chat_id, text="🎧 _Processando seu áudio..._", parse_mode='Markdown')
        file_info = await context.bot.get_file(update.message.voice.file_id)
        caminho = f"audio_{update.message.from_user.id}.ogg"
        await file_info.download_to_drive(caminho)
        descricao = transcrever_audio(caminho) or "Áudio inaudível"
        if os.path.exists(caminho): os.remove(caminho)
        usou_audio = True
    else:
        descricao = update.message.text
        usou_audio = False

    # Monta o dossiê para a IA
    dados = context.user_data
    prompt_final = (
        f"DADOS DO TÉCNICO:\n"
        f"- Topologia: {dados.get('topologia')}\n"
        f"- Sinal Medido: {dados.get('sinal')}\n"
        f"- Local da Medição: {dados.get('local')}\n"
        f"- Relato do Problema: {descricao}\n\n"
        f"Com base nisso, qual o diagnóstico e solução?"
    )

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    
    # Consulta o Lúmen
    resposta = consultar_lumen(prompt_final)
    
    # Envia Texto
    await context.bot.send_message(chat_id=chat_id, text=resposta)
    
    # Envia Áudio (se o técnico usou áudio ou se preferir sempre mandar)
    # Aqui configurei para mandar áudio se o técnico mandou áudio OU se a resposta for longa
    if usou_audio: 
        await gerar_e_enviar_audio(context, chat_id, resposta, update.effective_user.id)

    # Limpa a memória
    context.user_data.clear()
    
    await context.bot.send_message(chat_id=chat_id, text="✅ **Atendimento finalizado.** Digite /start para novo diagnóstico.", parse_mode='Markdown')
    
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela o processo."""
    await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Diagnóstico cancelado. Digite /start para recomeçar.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ==============================================================================
# 4. START (SETUP DO CONVERSATION HANDLER)
# ==============================================================================
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Configura a máquina de estados
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            TOPOLOGIA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_topologia)],
            SINAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_sinal)],
            LOCAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_local)],
            DESCRICAO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, finalizar_diagnostico),
                MessageHandler(filters.VOICE, finalizar_diagnostico)
            ],
        },
        fallbacks=[CommandHandler('cancelar', cancelar)]
    )

    application.add_handler(conv_handler)

    print("✅ LUX está ON!")
    application.run_polling()