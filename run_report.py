import argparse
import os
import json
from core.transcribe import Transcriber
from core.align import AlignmentEngine
from core.html_report import generate_html_report
import yaml

def main():
    parser = argparse.ArgumentParser(description="Genera reporte HTML de cortes para Auto Editor")
    parser.add_argument("--video", required=True, help="Ruta al video o audio")
    parser.add_argument("--script", required=True, help="Ruta al archivo de guion (.txt)")
    parser.add_argument("--output", default="reporte_cortes.html", help="Ruta de salida del HTML")
    args = parser.parse_args()
    
    if not os.path.exists(args.video):
        print(f"Error: No se encontró el video {args.video}")
        return
    if not os.path.exists(args.script):
        print(f"Error: No se encontró el guion {args.script}")
        return
        
    print(f"--- AUTO EDITOR: MODO REPORTE ---")
    print(f"Cargando configuración...")
    config = {}
    if os.path.exists("config.yaml"):
        with open("config.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            
    print(f"1. Transcribiendo video (usando caché si existe)...")
    transcriber = Transcriber("config.yaml")
    
    def log_progress(msg):
        print(f" > {msg}")
        
    transcript_data = transcriber.transcribe(args.video, progress_callback=log_progress)
    # WhisperX devuelve los segmentos. Aplanamos a lista de palabras:
    transcript_words = []
    for segment in transcript_data.get("segments", []):
        for word in segment.get("words", []):
            if "word" in word:
                transcript_words.append(word)
                
    if not transcript_words:
        print("Error: No se detectaron palabras en la transcripción.")
        return
        
    print(f"2. Leyendo guion...")
    with open(args.script, "r", encoding="utf-8") as f:
        script_text = f.read()
        
    print(f"3. Alineando guion vs audio ({len(transcript_words)} palabras)...")
    engine = AlignmentEngine(config)
    align_results = engine.align(script_text, transcript_words)
    
    print(f"   - Tomas duplicadas (Retakes): {len(align_results['retake_groups'])}")
    print(f"   - Tramos fuera de guion: {len(align_results['unmatched_segments'])}")
    print(f"   - Oraciones no encontradas: {len(align_results['skipped_sentences'])}")
    
    print(f"4. Generando Reporte HTML...")
    out_path = generate_html_report(align_results, transcript_words, args.output)
    
    print(f"¡Listo! Reporte guardado en: {os.path.abspath(out_path)}")
    print("Abre este archivo en tu navegador web para revisarlo manualmente.")

if __name__ == "__main__":
    main()
