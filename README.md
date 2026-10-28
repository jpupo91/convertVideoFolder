# Conversor de videos para TV JTC

Aplicacion de escritorio en Python y PyQt5 para convertir videos a MP4 con H.264 y audio AAC. Permite procesar varios archivos en paralelo, elegir la calidad y resolucion, y usar codificacion por GPU cuando esta disponible.

## Requisitos

- Python 3.8 o posterior.
- FFmpeg y FFprobe instalados y disponibles en el `PATH`.
- PyQt5, instalable con `requirements.txt`.

La codificacion por GPU depende del hardware, los controladores y de que la compilacion de FFmpeg incluya el codificador correspondiente. La aplicacion tambien ofrece codificacion por CPU.

## Instalacion

Clona el repositorio y entra en su carpeta:

```bash
git clone <URL_DEL_REPOSITORIO>
cd convertVideoFolder
```

Crea un entorno virtual e instala la dependencia de Python:

```bash
python -m venv .venv
```

Activa el entorno virtual:

```bash
# Linux o macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Instala PyQt5:

```bash
python -m pip install -r requirements.txt
```

Instala FFmpeg segun tu sistema operativo y comprueba que `ffmpeg` y `ffprobe` se puedan ejecutar desde una terminal:

```bash
ffmpeg -version
ffprobe -version
```

## Uso

Inicia la interfaz grafica con:

```bash
python convertir_videosQT.py
```

Selecciona una carpeta de entrada. La aplicacion busca videos directamente en esa carpeta (no en sus subcarpetas) y admite MP4, AVI, MKV, MOV, WMV, FLV, WEBM, M4V, MPG, MPEG, 3GP y TS. Elige la carpeta de salida o deja la predeterminada `convertidos_tv`.

Los videos se guardan como `<nombre>_tv.mp4`. Si ya existe un archivo de salida con ese nombre, se omite para no sobrescribirlo. Puedes ajustar el numero de conversiones paralelas, la calidad, la resolucion y el bitrate de audio. Los subtitulos externos compatibles (SRT, ASS, SSA, SUB y VTT) pueden seleccionarse manualmente o detectarse junto al video con nombres como `<video>.srt` o `<video>.es.srt`.

## Estructura

```text
convertVideoFolder/
├── convertir_videosQT.py
├── requirements.txt
├── .gitignore
└── README.md
```

