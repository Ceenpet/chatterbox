# Baseline estable experimental de Víctor

Aprobada por el usuario el 7 de octubre de 2026. Esta fase queda cerrada.
No cambiar estos valores ni volver a optimizar el fondo, salvo que una prueba
futura revele un problema real y se autorice el cambio. No hay integración con Nexo.

## Configuración de referencia

| Componente | Valor |
|---|---|
| S3Gen | `checkpoints_es_es/s3gen.pt` |
| T3 | `checkpoints_es_es/t3_es_es.safetensors` |
| Referencia de identidad | `mi_voz.wav` |
| Seed exacta | **1928081889104100** |
| Temperature | 0.60 |
| Exaggeration | 0.50 |
| CFG weight | 0.50 |
| Idioma | `es` |
| Dispositivo de síntesis | CUDA, entorno `chatterbox-v3` |
| Velocidad de reproducción | **1.00**, cadencia natural; sin WSOLA en la ruta normal |
| Fondo | Room tone sintético estacionario actual |
| Nivel de fondo | Aproximadamente **−64,9 dBFS** medidos durante la espera; objetivo del generador −65,00625508816927 dBFS |
| Transiciones de fondo | **60 ms**, suavizado de la ganancia complementaria actual |
| S3Gen `trim_fade` | Intacto |
| Modo predeterminado | `tranquilo`; `rapido` disponible explícitamente |

La seed se mantiene completa en los generadores que admiten ese rango; el motor
conserva la política RNG por segmento ya validada. El fondo usa un RNG local
independiente que no altera la seed ni el estado aleatorio de la voz.

## Flujo aprobado

Motor persistente → segmentador adaptativo actual → síntesis de cada segmento
→ audio a velocidad natural 1.00 → pausas lingüísticas de reproducción → preparación del fondo complementario → cola limitada a dos
segmentos → un único dispositivo abierto durante toda la respuesta.

PCM mono de 24 kHz, bloques de 40 ms y reserva actual de seis bloques (240 ms).
Tras la primera voz, las esperas reciben PCM de fondo continuo. Las pausas
naturales se conservan. No se añade fondo innecesario sobre segmentos que ya
contienen suficiente noise floor. Se mantiene la corrección localizada de cola
existente, sin editar la voz ni aplicar reducción de ruido general.

El fondo es ruido gaussiano independiente filtrado con un FIR fijo de 2049
coeficientes, con estado conservado entre bloques. El perfil procede únicamente
de estadísticas Welch suavizadas de tres regiones de `mi_voz.wav`; no copia
muestras, fases, respiraciones ni envolventes temporales, y no utiliza loops.
La forma del filtro coincide con la prueba sintética aislada aprobada.
La normalización del flujo continuo usa la energía del filtro, sin ajustes de
nivel por bloque. Se atenúan frecuencias inferiores a 60 Hz y superiores a 9,5 kHz.

`cancel()` detiene voz y fondo mediante el reset actual del dispositivo y vacía
el audio pendiente. Una síntesis GPU ya iniciada termina y se descarta; no existe
todavía interrupción interna de generación por tokens. Solo una respuesta puede
ejecutarse a la vez por coordinador/motor. Las métricas y la segmentación actuales
se conservan, con métricas adicionales de reserva, pausas y márgenes.

## Archivos activos

- `engine.py`: modelo CUDA persistente, configuración de identidad y RNG.
- `adaptive_segments.py`: modos y segmentación adaptativa.
- `segmenter.py`: función auxiliar de fronteras lingüísticas que importa el
  segmentador adaptativo; debe conservarse aunque su coordinador antiguo no se use.
- `conversation_timing.py`: velocidad natural, pausas lingüísticas y reserva inicial adaptativa.
- `speed_comparison.py`: WSOLA histórico/experimental; no se invoca en la ruta normal.
- `synthetic_room_tone.py`: generador sintético continuo y su filtro.
- `room_tone.py`: funciones de mezcla selectiva heredadas y corrección de cola;
  su generador de fragmentos grabados ya no es el fondo de la ruta activa.
- `live_continuity.py`: generación, cola, reproducción, cancelación y métricas.
- `continuous_audio.py` y `playback.py`: dispositivo WinMM y primitivas nativas.
- `demo_live_continuity.py`: prueba real y exportación exacta del PCM enviado.
- `demo_adaptive.py`: pruebas de ambos modos sobre esta ruta.

Dependencias de datos: los dos checkpoints y `mi_voz.wav` indicados arriba,
además de `room_tone_profile.json`, que
actualmente aporta los intervalos estadísticos y es necesario para inicializar
el fondo. No se reproduce el audio de esos intervalos.
El paquete `src/chatterbox` contiene el motor T3/S3Gen subyacente y permanece intacto.
`victor_habla.py` permanece intacto y no es el coordinador de esta ruta.
Los antiguos `pipeline.py` y `demo.py` no representan este baseline.

### Reconstrucción desde una copia limpia

Usar el entorno documentado en `../chatterbox-v3_environment.yml` y el paquete
local `src/chatterbox` instalado según `../pyproject.toml`, con CUDA disponible.
Aportar externamente `mi_voz.wav` en la raíz y estos recursos en `checkpoints_es_es/`:
`s3gen.pt`, `t3_es_es.safetensors`, `ve.pt` y
`grapheme_mtl_merged_expanded_v1.json`. `conds.pt` es un condicionamiento opcional
del loader; la voz de Víctor se prepara con `mi_voz.wav` en todo caso.
El tokenizer intenta obtener una tabla Cangjie de su caché Hugging Face; si no
está disponible en modo offline, registra un aviso y continúa sin esa conversión
china, que no se utiliza para el idioma `es` de esta baseline.
No se necesitan snapshots, WAVs históricos ni informes de pruebas para el flujo
activo o sus demos. Los recursos de modelos, la referencia y los resultados no
se versionan. Las menciones a resultados anteriores son únicamente evidencia
histórica; esos archivos no estarán presentes en un clon limpio.

Ejecutar `python -B -m victor_experimental.demo_live_continuity` o
`python -B -m victor_experimental.demo_adaptive --compare` desde la raíz.
El módulo `speed_comparison` conserva WSOLA para diagnóstico; su CLI de comparación offline
es opcional y requiere indicar explícitamente `--source` y `--metrics` de una
prueba propia. No es una dependencia para generar o reproducir voz.

## Evidencia de aceptación

Prueba real aprobada:
`../victor_real_083_continuo_20261007_144810_1d178be7.wav`.
Informe: archivo del mismo nombre con extensión `.json`.
Validación: `../victor_real_083_continuo_20261007_144810_1d178be7_synthetic_validation.json`.

Tres segmentos, modo `rapido`, primer audio 4,082 s después de iniciar la
respuesta con el modelo ya cargado, duración 27,68675 s, 6 s de espera con
fondo, RMS durante la espera −64,895 dBFS y cero underruns observados.
Síntesis: 3,857 / 9,012 / 6,115 s; ratio síntesis/voz a 0.83: 0,8754.
VRAM máxima asignada: 3,543 GiB. El WAV contiene exactamente los 664482 frames
enviados y consumidos por el dispositivo; no es una captura acústica de loopback.
Cancelación y captura exacta verificadas con pruebas nativas.

El usuario acepta una diferencia mínima entre el fondo de la voz y el fondo
sintético durante los silencios: no rompe la sensación de conversación. No se
requiere corregirla. La aceptación perceptiva no garantiza ausencia de problemas
en todos los textos o cargas futuras.

## Pendiente para una conversación de texto → voz

1. Adaptador de entrada de respuestas reales: primero texto completo; si llega
   incrementalmente de un LLM, acumulador que entregue unidades lingüísticas
   completas al planificador sin rehacer la segmentación aprobada.
2. Coordinador de turnos con motor persistente: identificadores de respuesta,
   política de nueva petición/interrupción, cancelación y espera del trabajo GPU
   anterior antes de sustituirlo; evitar audio de turnos obsoletos.
3. Contrato de integración para texto, modo explícito, fin de respuesta,
   cancelación, errores y publicación de las métricas existentes.
4. Validación conjunta con el generador de texto bajo carga real: VRAM compartida,
   latencias, reservas, pausas largas, cancelaciones y respuestas sucesivas.

No se implementa ninguna de esas piezas en esta fase. La entrada por voz y el
reconocimiento de habla serían una capa adicional, no necesaria para texto → voz.

## Historial: velocidad 0.85 y pausas lingüísticas

Las métricas anteriores a esta sección son evidencia histórica a 0.83.
Aquella reproducción usaba WSOLA a 0.85 con pitch preservado. La segmentación
mantiene sus reglas: cada límite `sentence` no final añade 350 ms; `clause` y
`discourse_comma` añaden 300 ms. Un corte forzado entre palabras por longitud
no recibe pausa adicional. No se añade pausa al final de la respuesta.
Las pausas son PCM planificado cubierto por el mismo room tone sintético y los
mismos fundidos de 60 ms. Chatterbox no recibe instrucciones de generar silencio.
El productor no espera esas pausas: continúa sintetizando sobre la misma cola.
Las métricas separan duración del segmento de voz, pausa añadida y espera real.

El servicio permanece LOADING durante carga, acondicionamiento y warm-up CUDA
sin reproducción. Solo anuncia READY tras éxito; un fallo de warm-up produce
ERROR/WARMUP_FAILED. El warm-up restaura Python, NumPy y Torch CPU/CUDA RNG en
finally; cada síntesis real sigue restaurando la seed exacta y el estado original
posterior al acondicionamiento. No cambia los parámetros de identidad.

### Validación HTTP real de la referencia 0.85 (2026-10-07)

Motor inicializado una vez; READY tras 44,09 s incluyendo imports, carga,
acondicionamiento y warm-up (7,39 s, sin reproducción). Primera respuesta:
audio a 7,40 s; cancelación confirmada por dispositivo en 7,48 ms y GPU drenada
antes de admitir el siguiente turno. Cancelar el ID antiguo no afectó al nuevo.
Segunda respuesta tranquilo: primer audio 7,55 s, síntesis 7,22/5,45/6,74 s;
segmentos reproducidos 7,06/5,60/6,92 s; pausas sentence/clause 350/300 ms.
Duración total 20,23 s; ratio síntesis/salida 0,960; márgenes 1,82/0,77 s;
espera de generación 0; fondo activo 2,10 s; underruns observados 0;
pico VRAM asignada 3,48 GiB. Durante la primera pausa continuó la síntesis
CUDA; al llegar a la segunda ya había terminado. Los márgenes se estiman con
contador del dispositivo muestreado a unos 40 ms, no con loopback acústico.

Una prueba adicional rapido con una segunda unidad de 173 caracteres necesitó
6,12 s de espera cubierta por room tone (sin underruns). Las pausas no garantizan
margen suficiente para cualquier texto; se conservaron las reglas de segmentación.
El objetivo de latencia 5–7 s tampoco es una garantía de rendimiento.
PCM exportado y verificado por hash y muestras consumidas/enviadas, resultados
locales bajo victor_service_runs/ (excluidos de Git). Nexo no se modificó.

## Referencia conversacional natural aprobada

La referencia principal actual es 1.00, sin invocar WSOLA. La evidencia auditiva
aprobada es victor_service_252b71136a134303a76fe7c50ba9b69a_extended-turn.wav;
el archivo es evidencia local y no es dependencia de ejecución ni se versiona.
La puntuación del planificador llega intacta al motor: no sustituye signos para
manipular prosodia. Las menciones a 0.83/0.85 en pruebas anteriores son históricas.
Las pausas de 350 ms en frase y 300 ms en cláusula/coma discursiva son de
reproducción; el modelo no recibe instrucciones de generar silencio.

conversation_timing.py define la reserva inicial adaptativa. Tranquilo es el modo
predeterminado. Rapido queda disponible explícitamente. Si falta margen, se
priorizan planificación, buffering, equilibrio y look-ahead; no se modifica la
velocidad aprobada. La introducción corta seguida de una unidad costosa puede
necesitar más espera inicial (12.15 s observados y aceptados).

La carga del servicio incluye acondicionamiento y warm-up sin reproducción;
READY solo se anuncia tras éxito. La seed y estados RNG siguen restaurándose
por segmento. El contrato HTTP y una única respuesta activa se conservan.

Diagnóstico opcional: arrancar voice_http con --capture-dir <directorio> exporta
PCM exacto; añadir --capture-stages conserva además raw y processed por segmento,
y sus matrices NPY sin pérdida. Raw es salida del motor con sus tratamientos
internos intactos, antes del procesamiento de reproducción. Processed se captura
tras el tratamiento existente de cola, antes de pausas y mezcla. Todo está
DESACTIVADO por defecto. No se hacen escrituras de archivos desde el callback
nativo de audio: se copian las etapas en el productor y se exportan tras run().
Una cancelación puede descartar parte del PCM ya enviado: el WAV de ese turno
no debe confundirse con una captura acústica de lo realmente oído.

Recuperación: baseline Git anterior 6859eb42c64a1b8411cea9da21ad411f4ebe7f7e;
versión intermedia 0.85 conservada en
snapshot_chatterbox_natural_before_20261007_212457/victor_experimental/.
El entorno, checkpoints y mi_voz.wav son recursos externos necesarios para
reproducir cualquiera de esas versiones. No volver a optimizar la voz aprobada.

## Validación final de integración aprobada

Prueba HTTP/CUDA: victor_service_runs/4213d2ede0524fc6a0fc4dd239f8b77a/report.json.
Modo tranquilo; warm-up sin reproducción 7.47 s; carga completa hasta READY
42.55 s. Una sola inicialización para turno cancelado, segundo turno completo
y respuesta larga. CANCELLING → CANCELLED; parada del dispositivo 3.79 ms;
GPU drenada en 4.25 s; cancelación antigua no afecta al siguiente turno.

Referencia: primer audio 8.00 s, duración 16.86 s, reserva inicial 1.38 s,
síntesis 6.58/4.88/6.31 s, márgenes 2.87/1.18 s, espera 0, underruns observados 0.
Respuesta larga: primer audio 7.48 s, duración 36.81 s, reserva inicial 1.07 s,
8 bloques; margen mínimo 1.13 s, espera 0, underruns observados 0.
Pico asignado 3.48 GiB. Ratios síntesis/salida 1.054 y 1.057: la reserva y la
síntesis previa al primer audio cubrieron el déficit en estas respuestas;
no se garantiza tiempo real indefinido ni competencia con otro motor GPU.

Captura opcional de las tres etapas validada. Las matrices brutas de referencia
son idénticas a la voz bruta previamente aprobada. Processed coincide exactamente
con aplicar únicamente el tratamiento histórico de cola sobre raw, sin WSOLA.
PCM final verificado por hash y contadores. La puntuación se conserva al dividir
y concatenar las unidades; no se introdujeron signos nuevos para prosodia.

42 tests aprobados: 36 de la implementación vigente y 6 históricos locales.
También se verificó por test que una llamada a WSOLA en la ruta natural falla
la prueba. Todas las capturas permanecen desactivadas por defecto.
No hay modificación de identidad de voz, Nexo, instalación, commit ni push.
