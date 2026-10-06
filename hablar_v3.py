import torch
import torchaudio
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

print("Cargando Chatterbox V3...")
device = "cuda" if torch.cuda.is_available() else "cpu"
print("Dispositivo:", device)

model = ChatterboxMultilingualTTS.from_pretrained(
    device=device,
    t3_model="v3"
)

texto = (
    "Hola. Esta es mi primera prueba hablando en español de España. "
    "Quiero comprobar si ahora mi pronunciación y mi acento suenan naturales."
)

print("Generando voz...")

audio = model.generate(
    texto,
    language_id="es"
)

torchaudio.save(
    "voz_espanol_v3.wav",
    audio,
    model.sr
)

print("Audio creado: voz_espanol_v3.wav")
input("Pulsa Enter para terminar...")