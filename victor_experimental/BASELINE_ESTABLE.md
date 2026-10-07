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
| Velocidad de reproducción | **0.83**, pitch preservado mediante WSOLA |
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
→ WSOLA a 0.83 → preparación del fondo complementario → cola limitada a dos
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
se conservan sin cambios.

## Archivos activos

- `engine.py`: modelo CUDA persistente, configuración de identidad y RNG.
- `adaptive_segments.py`: modos y segmentación adaptativa.
- `segmenter.py`: función auxiliar de fronteras lingüísticas que importa el
  segmentador adaptativo; debe conservarse aunque su coordinador antiguo no se use.
- `speed_comparison.py`: implementación WSOLA reutilizada por el flujo real.
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
El módulo `speed_comparison` aporta WSOLA al flujo; su CLI de comparación offline
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
