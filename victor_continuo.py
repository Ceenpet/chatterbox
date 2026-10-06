
import torch
import soundfile as sf
import random
import numpy as np
import time
import winsound
import threading
import re
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

SEED = 1928081889104100
TEMPERATURE = 0.60
EXAGGERATION = 0.50
CFG_WEIGHT = 0.45
REFERENCIA = "mi_voz.wav"

device = "cuda" if torch.cuda.is_available() else "cpu"
print("Dispositivo:", device)
print("Cargando la voz de Victor...")

model = ChatterboxMultilingualTTS.from_local(
    "checkpoints_es_es",
    device=device,
    t3_model="t3_es_es.safetensors"
)

print("Victor esta preparado.")
if device == "cuda":
    print("VRAM ocupada por Chatterbox: %.2f GB" % (torch.cuda.memory_allocated() / 1024**3))
print("Escribe texto. Se dividira automaticamente por comas y signos.")
print("Para terminar escribe: salir")

def fijar_seed():
    random.seed(SEED)
    np.random.seed(SEED % (2**32))
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)

def generar(texto, archivo):
    fijar_seed()
    inicio = time.perf_counter()
    wav = model.generate(
        texto,
        language_id="es",
        audio_prompt_path=REFERENCIA,
        exaggeration=EXAGGERATION,
        cfg_weight=CFG_WEIGHT,
        temperature=TEMPERATURE
    )
    tiempo = time.perf_counter() - inicio
    audio = wav.squeeze().detach().cpu().numpy()
    sf.write(archivo, audio, model.sr, subtype="PCM_16")
    duracion = len(audio) / model.sr
    return tiempo, duracion

def dividir(texto):
    partes = []
    actual = ""
    for caracter in texto:
        actual += caracter
        if caracter in ",.;:!?":
            if actual.strip():
                partes.append(actual.strip())
            actual = ""
    if actual.strip():
        partes.append(actual.strip())
    return partes

while True:
    texto = input("\nTu: ").strip()
    if not texto:
        continue
    if texto.lower() == "salir":
        print("Cerrando Victor.")
        break

    partes = dividir(texto)
    print("Fragmentos:", len(partes))

    archivo_actual = "victor_chunk_0.wav"
    t, d = generar(partes[0], archivo_actual)
    print("Fragmento 1 generado en %.2f s | dura %.2f s" % (t, d))

    for i, parte in enumerate(partes):
        if i == 0:
            archivo = archivo_actual
        else:
            archivo = f"victor_chunk_{i}.wav"

        hilo = threading.Thread(
            target=winsound.PlaySound,
            args=(archivo, winsound.SND_FILENAME)
       )
        hilo.start()

        if i + 1 < len(partes):
            siguiente = f"victor_chunk_{i+1}.wav"
            t2, d2 = generar(partes[i+1], siguiente)
            print("Fragmento %d generado en %.2f s | dura %.2f s" % (i+2, t2, d2))

        hilo.join()

    print("Respuesta terminada.")
