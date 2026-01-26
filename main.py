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

# DEFININDO OS ESTADOS DA CONVERSA (Adicionado POLARIDADE)
TOPOLOGIA, SINAL_VALOR, POLARIDADE, LOCAL, DESCRICAO = range(5)

# ==============================================================================
# 2. FUNÇÕES AUXILIARES
# ==============================================================================

def limpar_texto_para_audio(texto):
    """
    1. Remove emojis e caracteres estranhos.
    2. Substitui termos técnicos para a fala ficar correta.
    """
    # Normaliza para minúsculas para facilitar substituição
    texto_falado = texto.lower()
    
    # SUBSTITUIÇÕES DE TERMOS (Dicionário de Fala)
    texto_falado = texto_falado.replace('roseta', 'saída do cliente')
    texto_falado = texto_falado.replace('onu', 'H G U') # Soletra ou fala HGU
    texto_falado = texto_falado.replace('dbm', 'de bê êmes') # Melhora pronúncia unidade
    
    # REMOÇÃO DE EMOJIS E SÍMBOLOS
    # Regex: Mantém apenas letras, números, pontuação básica e acentos. O resto (emojis) é apagado.
    padrao_permitido = r'[^\w\s,.?!;:\-\(\)áàâãéèêíïóôõöúçÁÀÂÃÉÈÊÍÏÓÔÕÖÚÇ]+'
    texto_falado = re.sub(padrao_permitido, '', texto_falado)
    
    # Limpezas extras (Markdown)
    texto_falado = re.sub(r'[\*\#_`]+', '', texto_falado) # Tira negrito/itálico
    texto_falado = re.sub(r'^- ', '', texto_falado, flags=re.MULTILINE) # Tira hifens
    texto_falado = re.sub(r'\s+', ' ', texto_falado).strip() # Tira espaços duplos
    
    return texto_falado

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
        
        # AQUI CHAMAMOS A NOVA FUNÇÃO DE LIMPEZA
        texto_tratado = limpar_texto_para_audio(texto)
        
        arquivo_saida = f"resposta_{user_id}.mp3"
        communicate = edge_tts.Communicate(texto_tratado, VOZ_NEURAL)
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
# 3. FLUXO DE CONVERSA (PASSO A PASSO ATUALIZADO)
# ==============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia o diagnóstico."""
    user = update.effective_user.first_name
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"👷 **Olá, {user}! Vamos iniciar o diagnóstico.**\n\n📍 **1. Qual é a Topologia da Rede?**",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([['Barramento', 'Balanceada (Splitter)']], one_time_keyboard=True)
    )
    return TOPOLOGIA

async def receber_topologia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda a topologia e pede o valor numérico do sinal."""
    context.user_data['topologia'] = update.message.text
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🔢 **2. Digite o valor do sinal medido:**\n(Apenas o número, ex: 24, 28, 30...)",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return SINAL_VALOR

async def receber_sinal_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda o número e pergunta a polaridade."""
    valor_digitado = update.message.text.replace(',', '.') # Garante formato decimal
    
    # Tenta validar se é número
    try:
        float(valor_digitado)
        context.user_data['sinal_numero'] = valor_digitado
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Por favor, digite apenas números.")
        return SINAL_VALOR

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🧲 **O valor {valor_digitado}dBm é Positivo ou Negativo?**",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([['Negativo (-)', 'Positivo (+)']], one_time_keyboard=True)
    )
    return POLARIDADE

async def receber_polaridade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calcula o sinal final e pergunta o local."""
    polaridade = update.message.text
    valor_bruto = float(context.user_data['sinal_numero'])
    
    # Lógica para garantir o sinal correto
    if 'Negativo' in polaridade:
        sinal_final = -abs(valor_bruto) # Força ser negativo
    else:
        sinal_final = abs(valor_bruto) # Força ser positivo
        
    context.user_data['sinal'] = f"{sinal_final} dBm"
    
    # Opções atualizadas conforme solicitado
    opcoes_local = [
        ['Interna/Cliente'],
        ['Saída Cliente/CTOP'], 
        ['Alimentação CTOP']
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🏠 **3. Onde você está medindo?**",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup(opcoes_local, one_time_keyboard=True)
    )
    return LOCAL

async def receber_local(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda o local e pede a descrição final."""
    context.user_data['local'] = update.message.text
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📝 **4. Descreva o problema ou mande um ÁUDIO.**",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    return DESCRICAO

async def finalizar_diagnostico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Coleta tudo, manda pra IA e encerra."""
    chat_id = update.effective_chat.id
    
    if update.message.voice:
        await context.bot.send_message(chat_id=chat_id, text="🎧 _Processando áudio..._", parse_mode='Markdown')
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
        f"DADOS TÉCNICOS COLETADOS:\n"
        f"- Topologia: {dados.get('topologia')}\n"
        f"- Sinal Medido (Confirmado): {dados.get('sinal')}\n"
        f"- Local da Medição: {dados.get('local')}\n"
        f"- Relato do Técnico: {descricao}\n\n"
        f"Analise e dê o diagnóstico."
    )

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    
    resposta = consultar_lumen(prompt_final)
    
    # 1. Envia Texto (com emojis visuais)
    await context.bot.send_message(chat_id=chat_id, text=resposta)
    
    # 2. Envia Áudio (Limpo, sem emojis e com termos corrigidos)
    if usou_audio: 
        await gerar_e_enviar_audio(context, chat_id, resposta, update.effective_user.id)

    context.user_data.clear()
    await context.bot.send_message(chat_id=chat_id, text="✅ **Fim.** /start para novo.", parse_mode='Markdown')
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Cancelado. /start para recomeçar.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ==============================================================================
# 4. START
# ==============================================================================
if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            TOPOLOGIA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_topologia)],
            SINAL_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_sinal_valor)],
            POLARIDADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_polaridade)],
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