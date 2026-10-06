from pathlib import Path
import torch
import torchaudio
from safetensors.torch import load_file as load_safetensors

from chatterbox.mtl_tts import ChatterboxMultilingualTTS
from chatterbox.models.t3 import T3
from chatterbox.models.t3.modules.t3_config import T3Config
from chatterbox.models.s3gen import S3Gen
from chatterbox.models.tokenizers import MTLTokenizer
from chatterbox.models.voice_encoder import VoiceEncoder
from chatterbox.mtl_tts import Conditionals


device = "cuda"

BASE = Path(
    r"C:\Users\cop3\.cache\huggingface\hub\models--ResembleAI--chatterbox"
    r"\snapshots\5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
)

ESPANA = Path(
    r"C:\Users\cop3\.cache\huggingface\hub\models--ResembleAI--Chatterbox-Multilingual-es-es"
    r"\snapshots\7262b364e3222ba9446c9289e7485eadc47ed902"
)

print("Cargando Voice Encoder...")
ve = VoiceEncoder()
ve.load_state_dict(
    torch.load(BASE / "ve.pt", weights_only=True)
)
ve.to(device).eval()

print("Cargando T3 específico de España...")
t3 = T3(T3Config.multilingual())

t3_state = load_safetensors(
    ESPANA / "t3_es_es.safetensors"
)

if "model" in t3_state:
    t3_state = t3_state["model"][0]

t3.load_state_dict(t3_state)
t3.to(device).eval()

print("Cargando S3Gen V3...")
s3gen = S3Gen()

resultado = s3gen.load_state_dict(
    torch.load(
        ESPANA / "s3gen_v3.pt",
        map_location="cpu",
        weights_only=True
    ),
    strict=False
)

print("Claves auxiliares no incluidas en el checkpoint:", resultado.missing_keys)

s3gen.to(device).eval()

print("Cargando tokenizer...")
tokenizer = MTLTokenizer(
    str(BASE / "grapheme_mtl_merged_expanded_v1.json")
)

print("Cargando voz predeterminada...")

conds = Conditionals.load(
    BASE / "conds.pt",
    map_location="cpu"
).to(device)

model = ChatterboxMultilingualTTS(
    t3,
    s3gen,
    ve,
    tokenizer,
    device,
    conds=conds
)

texto = (
    "Hola. Esta vez estoy utilizando un modelo específico para el español de España. "
    "Quiero comprobar si mi pronunciación y mi acento suenan naturales."
)

print("Generando voz española...")

audio = model.generate(
    texto,
    language_id="es",
    exaggeration=0.25,
    cfg_weight=0.35,
    temperature=5,
    repetition_penalty=1.2,
    min_p=0.05,
    top_p=1.0
)

torchaudio.save(
    "voz_espana.wav",
    audio,
    model.sr
)

print("Audio creado correctamente: voz_espana.wav")
#input("Pulsa Enter para terminar...")