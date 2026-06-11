import os
import json
import hashlib
import subprocess
import tempfile
import yaml
import logging
import shutil

try:
    import whisperx
except ImportError as e:
    import traceback
    err_tb = traceback.format_exc()
    raise ImportError(
        f"Error Crítico: No se pudo importar 'whisperx'.\n"
        f"Esto puede deberse a que no está instalado o una de sus dependencias (como torch o ctranslate2) está rota.\n"
        f"Instálalo en tu entorno de Anaconda usando:\n"
        f"/opt/anaconda3/envs/autoeditor/bin/pip install whisperx\n\n"
        f"Error original:\n{err_tb}"
    )

logger = logging.getLogger(__name__)

class Transcriber:
    def __init__(self, config_path="config.yaml"):
        # Load config
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f)
        else:
            self.config = {}

        self.cache_dir = os.path.expanduser(self.config.get("paths", {}).get("cache_dir", "~/.f24_cache/transcripts"))
        os.makedirs(self.cache_dir, exist_ok=True)
        
        whisper_conf = self.config.get("whisper", {})
        self.model_size = whisper_conf.get("model_size", "medium")
        self.language = whisper_conf.get("language", "es")
        self.compute_type = whisper_conf.get("compute_type", "int8")
        self.device = whisper_conf.get("device", "cpu")
        self.ffmpeg_path = self._find_ffmpeg()

    def _find_ffmpeg(self) -> str:
        # 1. Check config file
        configured_path = self.config.get("paths", {}).get("ffmpeg_path", "")
        if configured_path and os.path.exists(configured_path):
            return configured_path
            
        # 2. Check shutil.which
        path = shutil.which("ffmpeg")
        if path:
            return path
            
        # 3. Check common Mac paths
        common_paths = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
        for p in common_paths:
            if os.path.exists(p):
                return p
                
        # Not found
        raise FileNotFoundError(
            "Error Crítico: No se pudo encontrar el binario de 'ffmpeg' en el sistema.\n"
            "Es necesario para extraer el audio del video.\n"
            "Si usas macOS, instálalo ejecutando en tu terminal: brew install ffmpeg\n"
            "O define la ruta absoluta en 'ffmpeg_path' dentro de config.yaml."
        )

    def _extract_audio(self, video_path: str) -> str:
        """Extrae el audio a 16kHz wav usando ffmpeg"""
        temp_audio = tempfile.mktemp(suffix=".wav")
        cmd = [
            self.ffmpeg_path, "-y", "-i", video_path, 
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", 
            temp_audio
        ]
        logger.info(f"Extracting audio from {video_path}...")
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return temp_audio

    def _hash_file(self, file_path: str) -> str:
        """Genera un MD5 del archivo de audio"""
        hasher = hashlib.md5()
        with open(file_path, 'rb') as afile:
            buf = afile.read(65536)
            while len(buf) > 0:
                hasher.update(buf)
                buf = afile.read(65536)
        return hasher.hexdigest()

    def transcribe(self, media_path: str, progress_callback=None) -> dict:
        """
        Transcribe el audio y aplica forced alignment con WhisperX.
        Retorna un diccionario con los segmentos alineados por palabra.
        """
        if not os.path.exists(media_path):
            raise FileNotFoundError(f"Archivo no encontrado: {media_path}")

        # Extraer audio
        if progress_callback: progress_callback("Extrayendo audio para análisis...")
        audio_path = self._extract_audio(media_path)
        
        # Generar hash y revisar caché
        file_hash = self._hash_file(audio_path)
        cache_file = os.path.join(self.cache_dir, f"{file_hash}.json")
        
        if os.path.exists(cache_file):
            if progress_callback: progress_callback("Cargando transcripción desde caché...")
            logger.info("Cargando desde caché...")
            os.remove(audio_path)
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)

        try:
            # 1. Transcribir
            if progress_callback: progress_callback(f"Transcribiendo con modelo {self.model_size} (puede demorar)...")
            logger.info(f"Cargando modelo whisperx: {self.model_size} en {self.device}")
            model = whisperx.load_model(self.model_size, self.device, compute_type=self.compute_type, language=self.language)
            
            audio = whisperx.load_audio(audio_path)
            result = model.transcribe(audio, batch_size=8)
            
            # Liberar modelo VRAM/RAM (útil si se usó CUDA o MPS)
            import gc
            del model
            gc.collect()
            
            # 2. Forced Alignment
            if progress_callback: progress_callback("Alineando palabras exactamente (Forced Alignment)...")
            logger.info("Aplicando forced alignment...")
            model_a, metadata = whisperx.load_align_model(language_code=self.language, device=self.device)
            result = whisperx.align(result["segments"], model_a, metadata, audio, self.device, return_char_alignments=False)
            
            # Liberar modelo de alineación
            del model_a
            gc.collect()

            # Guardar en caché
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

            return result

        except Exception as e:
            logger.error(f"Error en transcripción: {e}")
            raise
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)
