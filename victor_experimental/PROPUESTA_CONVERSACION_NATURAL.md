# Conversación natural aprobada

La candidata fue aprobada por escucha y es la referencia principal documentada en BASELINE_ESTABLE.md.
Se conserva copia completa del código/configuración anterior bajo
`snapshot_chatterbox_natural_before_20261007_212457/victor_experimental/`.
No hay commit ni push. Nexo permanece intacto.

## Decisión

Reproducir a velocidad natural 1.00, sin invocar WSOLA. Conservar la identidad,
seed 1928081889104100, temperature 0.60, exaggeration 0.50, cfg_weight 0.50,
s3gen.pt, t3_es_es.safetensors, referencia mi_voz.wav e idioma es.
Mantener el fondo sintético, sus niveles y transiciones de 60 ms, trim_fade,
corrección histórica de impulso aislado en cola, cola limitada y cancelación.

El mismo servicio HTTP conserva el contrato y el motor persistente. Primero
completa carga, acondicionamiento y warm-up sin reproducción. El texto incremental
sigue acumulándose hasta end_of_response=true; esta fase no añade streaming de
texto ni streaming interno de tokens T3/S3Gen.

## Margen sin ralentizar la articulación

`conversation_timing.py` concentra la política temporal. Las pausas extra siguen
siendo 350 ms en fin de frase y 300 ms en cláusula/coma discursiva, solo entre
segmentos no finales. No se añade pausa a cortes forzados entre palabras.
El productor trabaja durante las pausas y durante la reserva inicial.

En tranquilo se retiene el inicio, normalmente 0.6–3 s tras disponer de la
primera unidad, según su ratio real síntesis/duración. Una introducción corta
seguida de una unidad mucho mayor permite hasta 12 s de reserva; se empieza
antes si la siguiente unidad completa ya está lista. Respuestas de una unidad
y modo rapido no reciben esa espera extra. La espera es cancelable y no envía
room tone como un supuesto primer audio. El dispositivo está abierto y pausado.

La selección adaptativa ahora penaliza el coste estimado del candidato realmente
elegido cuando supera la reserva disponible. Antes solo acotaba la longitud
objetivo: podía seleccionar dos frases cuyo coste no cabía en el margen.
Se conservan límites lingüísticos, abreviaturas y decimales. No se reescribe la
puntuación del texto. La normalización interna propia de Chatterbox permanece
sin modificar; en los tres textos de referencia no altera nada.

## Alternativas evaluadas

La comparación aislada reutilizó los mismos tres arrays brutos, misma mezcla y
pausas de 350/350 ms. No ejecutó CUDA nuevo ni mide rendimiento concurrente:
1.00: 16.86 s; 0.95: 17.71 s; 0.85: 19.71 s. Los ratios con los mismos costes
CUDA históricos fueron 0.953, 0.907 y 0.815. Las velocidades menores dan más
tiempo, pero modifican la cadencia del bruto que el usuario prefiere.

La primera prueba real natural funcionó en la respuesta corta. En la larga
agrupó dos frases y produjo 0.76 s de espera: motivó la corrección del coste
del candidato. Una ejecución con dos procesos CUDA parcialmente superpuestos
se descartó como referencia de rendimiento; se detuvo el segundo proceso y
se repitió la validación final con un único motor.

## Límites

No hay garantía de latencia ni margen para texto arbitrario. Una frase larga
puede requerir más espera inicial; si GPU no alcanza el tiempo real de forma
sostenida, un buffer finito no soluciona el déficit. Room tone conserva continuidad
en las esperas pero no las elimina. No se probó competencia sostenida con un LLM
de Nexo. Los márgenes usan el contador WinMM muestreado a unos 40 ms, no loopback
acústico; underruns son los observados por la cola WinMM, no un contador hardware.
La naturalidad y cadencia fueron aprobadas por escucha. No continuar optimizando la voz.

## Validación final aislada HTTP/CUDA

Resultados locales: victor_service_runs/fa70166e0d41498ab4399c2c0ec884bf/report.json
(y resumen summary.json). Un servicio, un trabajador y una inicialización de motor.
Carga completa incluyendo warm-up: 54.37 s; warm-up sin reproducción: 6.00 s.
Cancelación: CANCELLING → CANCELLED; parada del dispositivo 2.29 ms;
GPU drenada tras 2.86 s. Cancelación antigua no afectó al turno nuevo.
Tres respuestas posteriores ejecutadas sin recargar, exportando PCM exacto.

| Caso | Primer audio | Reserva inicial extra | Duración | Síntesis total | Ratio | Margen mínimo | Espera generación | Underruns |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Referencia, tres unidades | 6.54 s | 0.61 s | 16.86 s | 16.00 s | 0.949 | 1.45 s | 0 | 0 |
| Respuesta larga, siete bloques / ocho frases | 6.60 s | 0.60 s | 34.94 s | 32.99 s | 0.944 | 1.45 s | 0 | 0 |
| Introducción corta + unidad larga | 12.15 s | 8.66 s | 18.70 s | 17.73 s | 0.948 | 3.25 s | 0 | 0 |

El tiempo hasta primer audio comienza al ejecutar la respuesta; no incluye carga
inicial del servicio. Duración de salida tampoco incluye espera inicial.
Pico VRAM asignada en los tres turnos: 3.54 GiB. Fondo activo respectivamente
1.95, 5.11 y 1.90 s (incluye pausas planificadas y zonas de floor complementario).
Se confirmó síntesis CUDA en curso durante varias pausas de 350 ms; cuando ya
había terminado, las pausas se conservaron igualmente, sin acelerar la respuesta.

Todos los WAV coinciden con el hash de PCM enviado y los contadores de muestras
consumidas/enviadas. natural_voice_identity_check.json verifica que la respuesta
corta es bitidéntica a reconstruir el bruto aprobado de la prueba controlada,
con la misma corrección histórica de cola, floor y pausas, sin WSOLA.
Así se conserva objetivamente la articulación del bruto, no solo su pitch medio.

40 tests aprobados: protocolo, estados, warm-up/RNG, cancelación, reserva,
selección de candidatos, puntuación, audio nativo y continuidad. Los resultados
no garantizan margen bajo otras cargas GPU. La prueba larga dura 34.94 s y no
se extrapola a respuestas indefinidas. Se recomienda tranquilo a velocidad natural;
rapido conserva inicio sin reserva extra y puede necesitar más room tone de espera.

Código principal nuevo/modificado: conversation_timing.py, live_continuity.py,
adaptive_segments.py. Demos y captura diagnóstica adaptadas a ambos caminos
(rate 1: bypass; rate distinto: una aplicación WSOLA). No cambia el contrato HTTP,
VoiceEngine, checkpoints, referencia, seed ni parámetros de identidad.
Los WAV y resultados permanecen excluidos de Git. No se instala ninguna dependencia.

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
