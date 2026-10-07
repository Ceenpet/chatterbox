# Interfaz local de voz v1

Servicio independiente de Nexo. Usa la baseline de `BASELINE_ESTABLE.md` sin
exponer parámetros acústicos. No importa código de Nexo. El cliente usa únicamente
la biblioteca estándar y se puede ejecutar desde otro entorno Python.

## Arranque

Desde la raíz de Chatterbox, con el entorno `chatterbox-v3` y los recursos externos
documentados en la baseline. Configurar `VICTOR_VOICE_TOKEN` mediante un canal
local de confianza, con un valor aleatorio de 32-256 caracteres ASCII sin espacios.
El cliente necesita el mismo valor. No incluirlo en Git, URLs, comandos con el
token literal, logs ni documentación. El servicio no imprime ese valor.

```powershell
& C:/Users/cop3/miniconda3/envs/chatterbox-v3/python.exe -B -m victor_experimental.voice_http
```

HTTP solo en `127.0.0.1:8769`; `--port` cambia el puerto, no la dirección.
El socket se abre antes de cargar el motor. Un único trabajador carga
`VoiceEngine` y reutiliza esa instancia para todas las respuestas. Los hilos HTTP
solo autentican, validan y gestionan solicitudes. `LiveContinuity` conserva su
único productor de síntesis por respuesta; nunca hay dos respuestas simultáneas.

Cliente en otra consola, con `VICTOR_VOICE_TOKEN` ya configurado:

```powershell
python -B -m victor_experimental.voice_client status
python -B -m victor_experimental.voice_client send --id turno-1 --text "Hola, César. Estoy aquí." --end --wait
python -B -m victor_experimental.voice_client cancel --id turno-1
python -B -m victor_experimental.voice_client shutdown
```

La CLI `send` requiere `--end` para texto completo; si se omite, el turno queda
en `RECEIVING`. Un cierre de cliente no repite ni cancela automáticamente audio.
El cliente puede consultar el ID y cancelar explícitamente tras reconectarse.
Una aplicación futura puede iniciar/detener este proceso y continuar sin voz si
no responde o falla. No se introduce esa integración en Nexo en esta fase.

## Contrato HTTP

Todas las solicitudes requieren `Authorization: Bearer <token>`.
POST requiere `Content-Type: application/json` y `Content-Length` único.
No hay CORS; se rechazan peticiones con cabecera `Origin`. No se acepta
transferencia chunked. Las conexiones reciben un timeout de 5 s.

| Método y ruta | JSON de entrada |
|---|---|
| `GET /v1/status` | Sin cuerpo |
| `POST /v1/responses` | `response_id` obligatorio; `text` por defecto `""`; `strategy` por defecto `"tranquilo"`; `end_of_response` por defecto `false` |
| `POST /v1/responses/{id}/text` | `text` y `sequence` obligatorios; `end_of_response` por defecto `false` |
| `POST /v1/responses/{id}/cancel` | `{}` |
| `GET /v1/responses/{id}` | Sin cuerpo |
| `POST /v1/shutdown` | `{}`; solicita parada y devuelve 202 antes de esperar al trabajador |

Solo se admiten esos campos. Ejemplo de respuesta completa:

```json
{"response_id":"turno-1","text":"Hola, César. Estoy aquí.","strategy":"tranquilo","end_of_response":true}
```

Límites: JSON de 65536 bytes; 20000 caracteres de texto acumulado; ID de
1-64 caracteres ASCII alfanuméricos con `_`, `.` o `-` (primero alfanumérico).
Solo `tranquilo` o `rapido`. `end_of_response` es booleano, no un entero.
No se admiten NUL, claves JSON duplicadas ni NaN/Infinity. Máximo 256 fragmentos
adicionales por turno y 256 IDs por instancia. No se eliminan IDs automáticamente
para no permitir una reproducción duplicada por un reintento tardío.

Un ID nuevo reserva el único turno disponible. Un turno incremental concatena
los fragmentos literalmente: el cliente debe aportar los espacios necesarios.
La secuencia inicial de `/text` es 1. Debe llegar en orden y aumenta de uno en uno.
No se sintetiza hasta `end_of_response=true`; se ejecuta `run()` una sola vez
con todo el texto acumulado. La respuesta final no puede estar vacía.

Reintento de creación con el mismo ID y la misma petición inicial: devuelve 200
con el estado existente; nunca reproduce de nuevo, incluso si terminó o fue
cancelado. El ID con otra petición inicial produce 409 `ID_CONFLICT`.
Reintento de un fragmento con igual secuencia/contenido/final: devuelve el estado
existente sin concatenarlo dos veces. Si cambia su contenido: 409
`SEQUENCE_CONFLICT`; si llega fuera de orden: 409 `OUT_OF_ORDER`.
Un turno cerrado no admite fragmentos nuevos: 409 `TURN_CLOSED`.
Un ID desconocido para consulta, fragmento o cancelación: 404 `UNKNOWN_ID`.
Cancelar un ID terminal es idempotente y no afecta al turno activo posterior.

Creación nueva: 201. Consultas, fragmentos, cancelación y reintentos: 200.
Errores devuelven `{"error":{"code":"...","message":"..."}}`:
400 validación, 401 token, 403 origen web, 404 ruta/ID desconocidos, 409
`BUSY`/conflictos/límite de historial, 413 JSON demasiado grande, 415 tipo de
contenido, 503 `NOT_READY` durante carga/error/parada. No se sustituyen turnos
automáticamente ni se conserva una cola ilimitada de respuestas.

## Estados y cancelación

Servicio: `LOADING → READY`, o `LOADING → ERROR`. Durante ejecución/drain: `BUSY`.
Apagado: `STOPPING → STOPPED`. `can_accept` solo es verdadero con motor preparado
y ningún turno abierto. Un turno en `RECEIVING` mantiene el servicio preparado
pero reserva ese turno y hace `can_accept=false`.

Turno: `RECEIVING → QUEUED → GENERATING → SPEAKING → FINISHED`, con salidas
`CANCELLING → CANCELLED` o `ERROR`. La solicitud de cancelación solo establece
flags; el hilo de reproducción realiza el reset. El observer opcional de
`LiveContinuity` solo deposita eventos ligeros en una `SimpleQueue`.
No hace red, JSON ni toma locks del servicio desde la reproducción.

`audio_stopped=true` puede observarse antes de `CANCELLED`: en ese intervalo
la GPU todavía está terminando trabajo que se descarta, el turno permanece
`CANCELLING` y el servicio `BUSY`. Solo al devolver `run()` después de cerrar
audio y unir el productor se libera el turno. Una cancelación previa al inicio
de `run()` también se conserva mediante un evento externo del turno.

Un error normal de turno queda registrado y libera el servicio para el siguiente.
Un error de carga requiere reiniciar el proceso. Si el productor no termina
tras la cancelación, el servicio queda en `ERROR` y no admite otra síntesis.
La parada espera al trabajador; no interrumpe a la fuerza una carga o kernel CUDA.

`instance_id` cambia en cada proceso. El historial/idempotencia dura esa instancia,
no sobrevive a una caída. No reintentar automáticamente turnos entre instancias:
podría repetir audio que ya se oyó. No hay cola persistente ni replay automático.

## Métricas y diagnóstico

`GET /v1/status` expone carga total (incluidos imports), inicializaciones del motor,
instancia y turno activo. El estado del turno incluye eventos de inicio/parada,
error y las métricas originales completas de `LiveContinuity` al terminar;
durante ejecución `metrics` permanece en `null`. Los eventos se consumen al
consultar/controlar el servicio y al terminar el turno; no dependen de callbacks
de red ni de un monitor que ejecute síntesis.

`SPEAKING` y primer audio se refieren a `waveOutRestart`, no a una medición de
loopback acústica. La espera de carga inicial se mide por separado. El servicio
no imprime texto de conversación, URLs de turnos ni tokens. Los informes de
captura son opcionales y contienen texto/estado: usar almacenamiento local apropiado.

`--capture-dir` exporta el PCM exactamente enviado al dispositivo; está desactivado
por defecto. En turnos cancelados el WAV puede contener PCM enviado pero descartado
por `waveOutReset`; no debe presentarse como grabación de lo realmente oído.
En turnos terminados se verifica que frames enviados y consumidos coinciden.

## Pruebas independientes

```powershell
python -B -m unittest victor_experimental.test_voice_service victor_experimental.test_voice_http -v
python -B -m victor_experimental.demo_voice_service
```

La segunda prueba necesita CUDA, modelos, referencia y salida Windows. Genera un
token aleatorio solo en memoria, inicia servicio y cliente en procesos diferentes,
observa `LOADING/READY`, reproduce/cancela un turno, espera su drain, recibe otro
incrementalmente, intenta una cancelación antigua y verifica finalización sin
recargar el modelo. Exporta PCM/informe en `victor_service_runs/`, excluido de Git,
y apaga el servicio. No necesita Nexo, ni instala dependencias.

### Primera ejecución real verificada

Informe local no versionado:
`../victor_service_runs/1a971e053486450fb7417795b034f453/report.json`.
El cliente observó `LOADING → READY`. Carga total inicial, incluyendo imports,
modelo y preparación: 129,687 s. Una sola inicialización para ambos turnos.
Primer audio del turno interrumpido: 4,447 s; reset de cancelación: 3,534 ms;
espera hasta terminar/descartar trabajo GPU y alcanzar `CANCELLED`: 8,484 s.
Durante esa espera se observó `audio_stopped=true` con turno `CANCELLING`.

Segundo turno: recibido incrementalmente y ejecutado una sola vez; primer audio
3,993 s, duración 28,40675 s, fondo por espera 6,72 s, cero underruns observados.
Síntesis por segmento: 3,817 / 9,700 / 6,560 s; pico VRAM asignada: 3,543 GiB.
Una cancelación repetida del ID anterior no afectó al segundo turno, que terminó
en `FINISHED`. Exportación exacta del PCM y apagado `STOPPED` verificados.
Los hashes de los componentes acústicos protegidos permanecieron intactos.
Estos tiempos describen esa ejecución, no constituyen garantías bajo otra carga.

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
