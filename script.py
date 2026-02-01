import pandas as pd
import time
import os
import requests
import numpy as np
import csv
from datetime import datetime, timedelta
from binance.client import Client
from dotenv import load_dotenv

# === CONFIGURACIÓN ===
client = Client(tld='com')
load_dotenv()

telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

UMBRAL_VOLUMEN = 70_000_000 
TIMEFRAMES = ['5m', '15m']
ARCHIVO_LOG = "historial_senales.csv"

# Diccionario para evitar spam de la misma moneda
alertas_enviadas = {}

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {"chat_id": telegram_chat_id, "text": mensaje, "parse_mode": "Markdown", "disable_web_page_preview": True}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

def registrar_en_csv(datos):
    """Guarda la señal en el CSV con columnas para auditoría."""
    archivo_existe = os.path.isfile(ARCHIVO_LOG)
    campos_auditoria = {"entrada_tocada": "PENDIENTE", "resultado": "EN_CURSO", "precio_final": ""}
    datos.update(campos_auditoria)
    try:
        with open(ARCHIVO_LOG, mode='a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=datos.keys())
            if not archivo_existe:
                writer.writeheader()
            writer.writerow(datos)
    except Exception as e:
        print(f"Error CSV: {e}")

def detectar_patron_completo(df):
    """Lógica de bandera con TP de rango y SL de media."""
    if len(df) < 30: return None
    
    df_mastil = df.iloc[-30:-12]
    df_bandera = df.iloc[-12:]
    
    inicio_mastil = df_mastil['open'].iloc[0]
    fin_mastil = df_mastil['close'].iloc[-1]
    cambio_mastil = (fin_mastil - inicio_mastil) / inicio_mastil
    
    techo = df_bandera['high'].max()
    suelo = df_bandera['low'].min()
    rango = techo - suelo
    media = (techo + suelo) / 2
    
    # Pendiente para filtrar ENSO (Slope)
    y = df_bandera['close'].values
    x = np.arange(len(y))
    slope, _ = np.polyfit(x, y, 1)
    
    es_estrecho = rango < (abs(fin_mastil - inicio_mastil) * 0.40)
    vol_ok = df_bandera['vol'].mean() < df_mastil['vol'].mean()
    
    tipo = None
    if es_estrecho and vol_ok:
        if cambio_mastil > 0.02 and slope < 0: tipo = "BULL"
        elif cambio_mastil < -0.02 and slope > 0: tipo = "BEAR"

    if tipo:
        if tipo == "BULL":
            entry = techo * 1.0005
            tp = entry + rango
            sl = media
        else:
            entry = suelo * 0.9995
            tp = entry - rango
            sl = media
        
        if (abs(entry - sl) / entry) > 0.03: return None

        return {
            "tipo": tipo, "entry": round(entry, 6), "tp": round(tp, 6), 
            "sl": round(sl, 6), "techo": round(techo, 6), 
            "suelo": round(suelo, 6), "media": round(media, 6)
        }
    return None

def ejecutar_bot():
    try:
        # Escaneo dinámico por volumen
        tickers = [t for t in client.futures_ticker() if t['symbol'].endswith('USDT') and float(t['quoteVolume']) >= UMBRAL_VOLUMEN]
    except Exception as e:
        print(f"Error API: {e}"); return

    print(f"--- Escaneo {datetime.now().strftime('%H:%M:%S')} | Monedas: {len(tickers)} ---")
    
    for ticker in tickers:
        moneda = ticker['symbol']
        vol_m = round(float(ticker['quoteVolume']) / 1_000_000, 2)
        
        for tf in TIMEFRAMES:
            id_alerta = f"{moneda}_{tf}"
            if id_alerta in alertas_enviadas and datetime.now() < alertas_enviadas[id_alerta] + timedelta(hours=2):
                continue

            try:
                intervalo = Client.KLINE_INTERVAL_5MINUTE if tf == '5m' else Client.KLINE_INTERVAL_15MINUTE
                bars = client.futures_klines(symbol=moneda, interval=intervalo, limit=50)
                df = pd.DataFrame(bars, columns=['time','open','high','low','close','vol','ct','qv','tr','tb','tq','i']).astype(float)
                
                info = detectar_patron_completo(df)
                if info:
                    alertas_enviadas[id_alerta] = datetime.now()
                    log_data = {
                        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "moneda": moneda, "tf": tf, "tipo": info['tipo'],
                        "entrada": info['entry'], "tp": info['tp'], "sl": info['sl'], "vol_24h_m": vol_m
                    }
                    registrar_en_csv(log_data)

                    link = f"https://www.binance.com/en/futures/{moneda}"
                    mensaje = (
                        f"*{info['tipo']} FLAG DETECTADA*\n"
                        f"`{moneda}` ({tf})\n\n"
                        f"▶️ *ENTRADA:* `{info['entry']}`\n"
                        f"✅ *TP (Rango):* `{info['tp']}`\n"
                        f"❌ *STOP LOSS:* `{info['sl']}`\n\n"
                        f"📐 *Niveles:* T:`{info['techo']}` | M:`{info['media']}` | S:`{info['suelo']}`\n\n"
                        f"🔗 [ABRIR EN BINANCE]({link})"
                    )
                    enviar_telegram(mensaje)
                    print(f"Alerta: {moneda} {tf}")
                
                time.sleep(0.05)
            except: continue

if __name__ == "__main__":
    while True:
        ejecutar_bot()
        time.sleep(300)