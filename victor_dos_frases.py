import torch
import soundfile as sf
import random
import numpy as np
import time
import winsound
import threading
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

SEED = 1928081889104100
TEMPERATURE = 0.60
EXAGGERATION = 0.50
CFG_WEIGHT = 0.50
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

texto1 = "Hola Cesar. Te estoy escuchando."
texto2 = "Dime que quieres probar ahora y seguimos."

print("\nGenerando primera frase...")
t1, d1 = generar(texto1, "victor_frase1.wav")
print("Frase 1 generada en %.2f s | duracion %.2f s" % (t1, d1))

print("Victor empieza a hablar. Mientras tanto genero la frase 2...")
hilo = threading.Thread(
    target=winsound.PlaySound,
    args=("victor_frase1.wav", winsound.SND_FILENAME)
)
hilo.start()

t2, d2 = generar(texto2, "victor_frase2.wav")
print("Frase 2 generada en %.2f s | duracion %.2f s" % (t2, d2))

hilo.join()

gap = max(0.0, t2 - d1)
if gap > 0:
    print("Pausa estimada antes de la frase 2: %.2f s" % gap)
else:
    print("La frase 2 estuvo lista antes de terminar la frase 1.")

winsound.PlaySound("victor_frase2.wav", winsound.SND_FILENAME)
print("\nPrueba terminada.")
