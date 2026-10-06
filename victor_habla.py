import torch
import soundfile as sf
import random
import numpy as np
import time
import winsound
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

SEED = 1928081889104100
TEMPERATURE = 0.60
EXAGGERATION = 0.50
CFG_WEIGHT = 0.50
REFERENCIA = "mi_voz.wav"
SALIDA = "victor_respuesta.wav"

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
print("Escribe una frase. Para terminar escribe: salir")

while True:
    texto = input("\nTu: ").strip()
    if not texto:
        continue
    if texto.lower() == "salir":
        print("Cerrando Victor.")
        break

    random.seed(SEED)
    np.random.seed(SEED % (2**32))
    torch.manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(SEED)

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
    sf.write(SALIDA, audio, model.sr, subtype="PCM_16")
    print("Generado en %.2f segundos. Victor habla:" % tiempo)
    winsound.PlaySound(SALIDA, winsound.SND_FILENAME)
