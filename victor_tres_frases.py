
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

frases = [
    ("Hola Cesar. Te estoy escuchando.", "victor_frase1.wav"),
    ("Dime que quieres probar ahora y seguimos.", "victor_frase2.wav"),
    ("Creo que esto ya empieza a sonar bastante natural.", "victor_frase3.wav"),
]

print("\nGenerando frase 1...")
t1, d1 = generar(*frases[0])
print("Frase 1 generada en %.2f s | duracion %.2f s" % (t1, d1))

hilo1 = threading.Thread(target=winsound.PlaySound, args=(frases[0][1], winsound.SND_FILENAME))
hilo1.start()

print("Mientras habla la frase 1, genero la frase 2...")
t2, d2 = generar(*frases[1])
print("Frase 2 generada en %.2f s | duracion %.2f s" % (t2, d2))
hilo1.join()

gap12 = max(0.0, t2 - d1)
print("Pausa estimada entre frase 1 y 2: %.2f s" % gap12)

hilo2 = threading.Thread(target=winsound.PlaySound, args=(frases[1][1], winsound.SND_FILENAME))
hilo2.start()

print("Mientras habla la frase 2, genero la frase 3...")
t3, d3 = generar(*frases[2])
print("Frase 3 generada en %.2f s | duracion %.2f s" % (t3, d3))
hilo2.join()

gap23 = max(0.0, t3 - d2)
print("Pausa estimada entre frase 2 y 3: %.2f s" % gap23)

winsound.PlaySound(frases[2][1], winsound.SND_FILENAME)
print("\nPrueba terminada.")
