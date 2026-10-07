# Víctor: generación progresiva experimental

La referencia vigente, aprobada el 7 de octubre de 2026, está documentada en
[BASELINE_ESTABLE.md](BASELINE_ESTABLE.md). Incluye el fondo sintético actual,
velocidad 0.83 y reproducción PCM continua. Ese documento prevalece sobre las
descripciones históricas de pruebas y fondos anteriores que siguen a continuación.
La fase de continuidad acústica está cerrada; no se optimiza de nuevo sin un
problema real detectado y autorización del usuario.

Para reconstruirla desde un clon limpio, consultar la sección de recursos externos
en `BASELINE_ESTABLE.md`. Las demos activas son `demo_live_continuity` y
`demo_adaptive`; no requieren audios históricos. El resto de este README conserva
la historia de las pruebas, incluyendo herramientas antiguas no versionadas y
comandos que no forman parte de la baseline vigente.

La voz está fijada: T3 `t3_es_es.safetensors`, S3Gen `s3gen.pt`, referencia
`mi_voz.wav`, seed **1928081889104100**, temperature 0.60, exaggeration 0.50,
cfg_weight 0.50, idioma `es`. CUDA es obligatoria. No se usa S3Gen V3 ni se
modifica ninguno de los scripts anteriores. No hay integración con Nexo.

## Ejecución

Desde la raíz del proyecto, usando el Python del entorno `chatterbox-v3`:

```powershell
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m victor_experimental.demo --export --validate-cache
```

El modelo y los condicionamientos se cargan una vez para la prueba. La
validación opcional compara, por separado, una síntesis con preparación original
y otra con condicionamientos reutilizados; interrumpe la prueba si cambia la
longitud o la diferencia de muestras supera 1e-5. Esta validación vuelve a
preparar la referencia una vez adicional; el flujo de producción no lo hace.

La seed exacta se reinicia por segmento, siguiendo las pruebas de frases
existentes. Se capturan los estados RNG antes/después de preparar la referencia,
y cada síntesis reutilizada comienza en el estado posterior que tendría el
script original. No se cambia precisión, muestreo, pasos de flow, marca de agua,
ganancia ni se añaden fundidos para ocultar las pausas.

`--interactive` mantiene la instancia cargada y acepta texto completo, `/cancel`
y `/quit`. Una petición nueva cancela la anterior automáticamente. La cola de
peticiones guarda solo la última pendiente; la de audio admite dos segmentos
pendientes, además del que se reproduce y del que se está sintetizando.

## Cancelación

El consumidor posee un único dispositivo WinMM. Comprueba cancelación cada
10 ms y utiliza `waveOutReset`. La entrega de un bloque y la cancelación se
serializan con el mismo bloqueo. Los resultados de síntesis cancelados y los
bloques pendientes antiguos se descartan. La síntesis GPU de una frase ya en
curso termina antes de atender otra petición: todavía no tiene cancelación
interna por tokens. Detener audio no significa que la GPU se haya detenido.

WinMM se usa como backend experimental disponible en Windows, sin nuevas
dependencias. Microsoft recomienda WASAPI para desarrollo moderno; el backend
está separado para poder sustituirlo después, sin cambiar síntesis ni colas.
Referencia: https://learn.microsoft.com/en-us/windows/win32/multimedia/using-an-callback-to-process-driver-messages

## Segmentación y reproducción

Se prioriza puntuación de frase, con protección básica de abreviaturas, horas y
decimales. Se unen unidades menores de 28 caracteres cuando caben y se limita a
180 caracteres, usando coma/raya solo si una frase excede ese límite, o espacio
como último recurso. Son heurísticas: URLs, abreviaturas ambiguas y listas
complejas pueden requerir ajustes. El modelo normaliza la puntuación como en el
script original. Las frases se sintetizan de forma independiente: conservar
parámetros no garantiza la misma prosodia que sintetizar todo el párrafo.

No se generan WAV intermedios. `--export` conserva explícitamente PCM para
depuración y genera un WAV nuevo con el audio enviado y los intervalos observados
entre entregas; no es una grabación de loopback. No incluye la espera inicial.
Sin exportación, el PCM ya consumido se libera y el buffer permanece limitado.
No se añade captura ilimitada en el modo interactivo.

## Métricas y límites

El JSON registra carga, preparación, consumo RNG, primer PCM disponible,
primera entrega al dispositivo, síntesis/duración/RTF por segmento, colas, gaps,
solapamiento y memoria CUDA. El inicio acústico real y el silencio contenido
dentro del audio no se miden por loopback: el tiempo de inicio es `waveOutWrite`
y el fin lo confirma el driver. La validación previa calienta la síntesis; el
informe lo indica. Los hashes de scripts protegidos se verifican al terminar.

Comprobaciones de comportamiento:

```powershell
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m unittest victor_experimental.test_pipeline -v
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m unittest victor_experimental.test_native_playback -v
```

La segunda prueba abre la salida real de Windows con PCM silencioso para comprobar
reset/cancelación y sustitución de respuesta sin generar voz.

## Primera ejecución verificada

Informe: `../victor_progresivo_20261006_213425_d69b85a5.json`.
Audio: `../victor_progresivo_20261006_213425_d69b85a5.wav`.

- Carga del modelo: 92.04 s; preparación: 3.78 s (una vez, antes de atender texto).
- Preparación sin consumo de RNG en los cuatro generadores inspeccionados.
- Validación de reutilización: audio exactamente igual, diferencia máxima 0.0.
- Primer PCM / entrega al dispositivo, modelo caliente: 6.109 / 6.110 s.
- Síntesis de los tres segmentos: 6.107 / 5.339 / 5.765 s.
- Duraciones: 6.00 / 4.28 / 5.88 s; RTF ponderado: 1.065.
- Gaps confirmados por el driver: 0.0006 / 0.8168 s, además de silencios que
  puedan formar parte de la voz generada.
- Síntesis solapada con reproducción: 5.340 / 4.289 s.
- Pico asignado PyTorch: 3.482 GiB; reservado: 3.871 GiB.
- Cancelación nativa con PCM silencioso: confirmada en aproximadamente 20 ms.
- Cinco pruebas del coordinador/segmentador y una prueba nativa superadas.
- Archivos originales verificados por hash e intactos; exportación PCM16 mono,
  24 kHz, 16.987 s, sin saturación.

La arquitectura adelanta el inicio respecto a esperar todos los segmentos,
pero RTF > 1 y una espera de 0.82 s indican que todavía no garantiza continuidad
natural para cualquier respuesta. La identidad/prosodia entre frases queda
pendiente de valoración perceptiva del usuario. No se ha probado convivencia
con Qwen/Ollama ni texto incremental en esta fase.

## Comparación aislada de velocidad

`speed_comparison.py` procesa el WAV existente: no importa TTS, no genera nueva
voz ni cambia la configuración o el coordinador. A es el archivo original sin
procesado; B/C son archivos nuevos a 0.95/0.93, PCM16 mono 24 kHz.

El método WSOLA usa ventanas de 50 ms, hop de salida de 10 ms y búsqueda de
similitud +/-12 ms. No cambia la frecuencia de muestreo, ni añade normalización,
ruido, room tone, fundidos adicionales o edición de pausas. La ventana y el
solapamiento pertenecen al propio algoritmo de time-stretch. Referencia técnica
del algoritmo: https://ffmpeg.org/doxygen/trunk/af__atempo_8c.html (no se ejecuta
FFmpeg ni se ha instalado ninguna dependencia).

Resultados: `../victor_velocidad_20261006_215614_76c5fcb3.json`.
La comparación del WAV completo también estira los silencios ya grabados: no
simula el hueco futuro de una cola concurrente. El informe separa ambas cosas.
En el modelo ideal, los primeros 10.28 s de voz ganan 0.541/0.774 s de margen y
el hueco de 0.817 s quedaría en 0.276/0.043 s. Si el procesado se ejecutase en
serie después de cada síntesis, el coste CPU medido proyecta aproximadamente
0.520/0.286 s de hueco. Son proyecciones con tiempos de síntesis constantes,
no mediciones de una nueva ejecución concurrente.

La calibración con tonos de 110, 173 y 237 Hz da desviaciones menores de 0.11
cents. La mediana firmada estimada sobre la voz es -1.73/-2.20 cents y no hay
saturación digital. Esto no certifica ausencia de artefactos perceptibles:
la elección de velocidad y la aprobación de calidad requieren escucha humana.

## Reproducción progresiva con continuidad acústica real

La nueva ruta experimental es `live_continuity.py`, independiente del antiguo
coordinador y de `victor_habla.py`. Usa el mismo `VoiceEngine` persistente y
`GoldenVoice` intacto: seed 1928081889104100, s3gen.pt, t3_es_es.safetensors,
mi_voz.wav, es, temperature 0.60, exaggeration/cfg_weight 0.50. S3Gen y su
trim_fade no se modifican. WSOLA se aplica una sola vez a cada PCM a 0.83.

```powershell
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m victor_experimental.demo_live_continuity
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m unittest victor_experimental.test_live_continuity -v
```

`LiveContinuity(engine, reference, capture=False).run(text)` admite una respuesta
activa. `cancel()` puede llamarse desde otro hilo: el consumidor hace reset de
WinMM, devuelve/vacía todos los buffers y descarta la generación en curso cuando
termina. No se inicia otra respuesta hasta que la anterior devuelve el control;
esto conserva un solo trabajador de síntesis y la política RNG. El motor se puede
reutilizar entre respuestas. No se ha integrado con Nexo ni con la entrada de
texto incremental de un LLM.

Un dispositivo se abre antes de generar y permanece abierto toda la respuesta.
El primer PCM se reproduce cuando llega la primera voz, sin insertar una espera
inicial de room tone. A partir de ahí, 6 buffers de 40 ms se mantienen en cola,
incluyendo room tone si aún no llegó la siguiente frase. Se empieza con la cola
precargada y se drena al final. La cancelación no espera a drenar el audio; usa
waveOutReset. La reserva de 240 ms cubre variaciones de planificación, no bloquea
la cancelación. Las pausas incluidas en cada segmento se conservan; las esperas
reales añaden exclusivamente fondo, sin cortar palabras ni acelerar frases.

`room_tone.py` extrae el mismo material aceptado en B: 0.31–0.81 s y
12.46–12.96 s de mi_voz.wav. Corrige DC solo en el fondo, remuestrea este a
24 kHz y calibra a -54.6216 dBFS. Varía los puntos de lectura de forma
determinista para evitar un bucle corto fijo; no utiliza ni modifica RNG de voz.
Los cruces entre material grabado duran 60 ms y tienen ganancia de potencia
constante. La fase del fondo avanza incluso cuando queda oculto por la voz.

A diferencia del WAV B, que sumaba fondo sobre toda la voz, aquí una envolvente
complementaria de energía añade solo el nivel que falta. Se estima energía en
20 ms y se suaviza la ganancia del fondo en 60 ms. La voz original no se filtra,
normaliza ni reduce de ruido. Los fades de salida/inicio afectan al fondo y el
trim_fade original de voz permanece. Se mantiene la corrección local aceptada
del transitorio tardío, solo si cumple umbrales conservadores de pico aislado y
entorno muy silencioso; cada aplicación figura en el informe.

Los intervalos tienen baja energía y periodicidad moderada (0.33/0.28); esto no
certifica ausencia de respiración reconocible ni de restos de voz. Ya proceden
de la B aprobada por el usuario. No se afirma haber realizado una escucha humana
adicional. La variación natural del material y la modulación de los cruces deben
valorarse escuchando el nuevo audio; no se fija aún un recurso definitivo.

La exportación concatena los bytes que se entregaron a waveOutWrite, en el
mismo camino de ejecución: no reconstruye tiempos ni añade silencios inferidos.
El número final de muestras consumidas por WinMM se compara con el PCM exportado.
Es una copia del PCM enviado y consumido, no una grabación de loopback; no incluye
efectos del mezclador de Windows, latencia física ni distorsión del dispositivo.
Los underruns se detectan al reponer sin ningún buffer pendiente: no hay acceso
con este backend a un contador de xruns físicos del hardware.

Primera prueba real con el texto original: `../victor_real_083_continuo_20261006_222901_a3e43df9.json`.
Primer audio enviado/reinicio: 6.613/6.615 s, fuera de la carga del modelo.
Síntesis 6.305/5.845/5.915 s; voz a 0.83: 7.229/5.157/7.084 s.
Duración enviada 19.470 s; ratio síntesis/voz 0.928; VRAM asignada máxima
3.481 GiB. No hubo esperas de generación entre frases ni underruns observados;
el fondo complementario estuvo activo 2.059 s en los silencios internos.
La segunda prueba emplea una primera frase más corta para ejercitar una espera
real de generación, sin retrasos artificiales. Cada ejecución conserva su
informe y audio propios.

Segunda prueba real: `../victor_real_083_continuo_20261006_223147_d112409e.json`.
Primer audio 3.791 s, síntesis 3.606/4.515/5.837 s, voz a 0.83
3.470/5.157/7.084 s. La salida incluye 1.36/0.88 s de room tone durante espera;
duración total 17.951 s, fondo complementario activo 4.163 s en total.
Ratio síntesis/voz a 0.83: 0.888; síntesis/salida con pausas: 0.778.
Pico CUDA asignado/reservado: 3.477/3.686 GiB. Cero underruns observados.
Se enviaron y consumieron exactamente 430820 muestras y se verificó el hash del
PCM exportado. La lectura final del dispositivo se hace antes del cierre;
waveOutReset reinicia el contador, por eso se conserva aparte la posición antes
de cancelar. Pruebas nativas de cancelación de voz y room tone: aproximadamente
1–2 ms para reset, sin generación TTS en esas pruebas.

La cola adelantada puede introducir hasta aproximadamente 240 ms de fondo ya
programado antes de una voz que acaba de llegar, más la granularidad de bloque.
En la segunda ejecución, las llegadas tardías respecto al final nominal del
segmento en el dispositivo fueron 1.187/0.693 s; la salida de fondo durante
espera fue 1.36/0.88 s. No son métricas intercambiables. El informe adicional
`../victor_real_083_continuo_20261006_223147_d112409e_validation.json` separa ambas.
La reserva prioriza evitar vaciados del dispositivo. Reducirla requeriría otra
prueba bajo carga; no se declara latencia acústica a partir de esos timestamps.

## Estrategias de segmentación adaptativa

`LiveContinuity(..., strategy="tranquilo")` es el modo predeterminado.
`run(text, strategy="rapido")` permite cambiarlo explícitamente por respuesta.
La selección de contexto queda fuera del generador/segmentador: un futuro
coordinador puede pasar cualquiera de esos nombres. No existe selección por IA
ni cambio automático entre modos en esta fase.

```powershell
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m victor_experimental.demo_adaptive --strategy tranquilo
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m victor_experimental.demo_adaptive --strategy rapido
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m victor_experimental.demo_adaptive --compare
```

El planificador `adaptive_segments.py` conserva candidatos lingüísticos, no una
lista fija de segmentos. Decide el siguiente bloque justo antes de sintetizarlo.
Los presets son independientes del motor y de la reproducción. Tranquilo busca
aproximadamente 70 caracteres en el primer bloque, con mínimo normal de 45;
rápido busca 34, con mínimo inicial de 28. Los límites guían la selección de
frases/cláusulas completas; no son cortes rígidos por caracteres. La latencia
inicial de 5–7 s es un objetivo práctico de tranquilo, no una garantía: depende
del texto, puntuación, longitud hablada y velocidad real del modelo. Una respuesta
completa corta no se retrasa artificialmente ni se rellena con texto.

Después del primer bloque, ambos modos utilizan margen de voz disponible y
estimaciones suavizadas del coste de generación/postprocesado por carácter.
El último tiempo de síntesis, duración a 0.83, ratio síntesis/duración y room
tone por espera se incluyen en cada decisión. Ante margen bajo, esperas nuevas
o síntesis lenta, se prefiere la siguiente unidad natural más corta. Si hay
reserva y RTF favorable se admiten bloques más largos, reduciendo cortes y coste
fijo de llamadas. Aumentar la longitud no arregla por sí solo un RTF superior a
1; si no existe un buen límite, se conserva la unidad lingüística y el room tone
cubre la espera. Las predicciones incluyen el coste de WSOLA y pueden equivocarse.

Se consideran puntos, interrogaciones, exclamaciones, punto y coma, dos puntos
y comas anteriores a ciertos conectores discursivos. Se protegen abreviaturas,
siglas con puntos, iniciales, decimales y horas; no se corta una enumeración por
todas sus comas. El máximo ordinario es 180 caracteres. Ante cláusulas muy largas
sin puntuación utilizable, se recurre a una frontera entre palabras y se marca
esa excepción en el informe; la preservación prosódica no se puede garantizar
en ese caso. Un token indivisible excepcionalmente largo no se parte por la mitad.
Las colas finales pequeñas se tienen en cuenta al elegir el bloque anterior.

El margen se mide en PCM de voz preparado, incluyendo voz actual, cola y PCM
ya generado todavía retenido por el productor, menos los frames de voz consumidos
por el dispositivo. Los buffers de room tone no cuentan como reserva de voz.
El consumidor consulta la posición de WinMM aproximadamente cada 40 ms; el
productor solo lee una instantánea protegida por lock. Los tiempos de room tone
en cada decisión reflejan PCM ya programado, incluidos como máximo los 240 ms
adelantados; los totales finales son PCM consumido. El límite de cola sigue en
dos segmentos. Solo un hilo sintetiza y reinicia la seed exacta por segmento.
No cambian el dispositivo, su reserva, WSOLA, room tone, fundidos o trim_fade.

La comparación final usa el mismo texto y el mismo motor CUDA persistente.
Una síntesis corta de calentamiento precede a ambos modos y figura en los
informes. No se espera artificialmente para alcanzar la latencia objetivo.
Los WAV guardan exclusivamente el PCM enviado, por lo que no incluyen la espera
inicial sin audio. Esa espera se mide y figura aparte.

Prueba: `../victor_adaptativo_20261006_225543_3eae30f0_comparison.json`.
Tranquilo: primer audio 6.598 s, 3 segmentos, 19.133 s de salida y ninguna espera
de generación; RTF 0.922, fondo complementario activo 2.219 s.
Rápido: primer audio 3.681 s, 4 segmentos, 23.174 s de salida, 3.800 s de fondo
durante esperas; RTF 0.977, fondo complementario activo 6.188 s.
Ambos: VRAM asignada máxima 3.477 GiB y cero underruns observados. Tranquilo
decidió con 0/6.892/6.974 s de reserva; rápido con 0/3.470/3.663/5.157 s.
La continuidad e identidad perceptivas deben compararse escuchando; los parámetros
y hashes protegidos son iguales, pero la segmentación puede variar la prosodia.

Pruebas de comportamiento y dispositivo:

```powershell
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m unittest victor_experimental.test_adaptive_segments victor_experimental.test_live_continuity -v
```
