import os

def get_context_words(transcript_words, start_idx, end_idx, context_window=15):
    """
    Retorna el texto anterior, el texto resaltado, y el texto posterior.
    Contexto medido en cantidad de palabras (aprox 15 palabras = 2-3 segundos).
    """
    ctx_start = max(0, start_idx - context_window)
    ctx_end = min(len(transcript_words) - 1, end_idx + context_window)
    
    before = " ".join([w["word"] for w in transcript_words[ctx_start:start_idx]])
    highlight = " ".join([w["word"] for w in transcript_words[start_idx:end_idx+1]])
    after = " ".join([w["word"] for w in transcript_words[end_idx+1:ctx_end+1]])
    
    return before, highlight, after

def generate_html_report(results, transcript_words, output_path="report.html"):
    html = [
        "<!DOCTYPE html>",
        "<html lang='es'>",
        "<head>",
        "<meta charset='UTF-8'>",
        "<style>",
        "body { font-family: 'Inter', sans-serif; background-color: #1e1e1e; color: #e0e0e0; padding: 20px; }",
        ".card { background-color: #2d2d30; padding: 15px; margin-bottom: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }",
        ".title { font-weight: bold; font-size: 1.2em; margin-bottom: 10px; color: #4da6ff; }",
        ".context { color: #888; font-size: 0.9em; }",
        ".highlight { background-color: #28a745; color: white; padding: 2px 4px; border-radius: 4px; font-weight: bold; }",
        ".highlight-bad { background-color: #dc3545; color: white; padding: 2px 4px; border-radius: 4px; text-decoration: line-through; }",
        ".highlight-improv { background-color: #ffc107; color: black; padding: 2px 4px; border-radius: 4px; }",
        ".meta { font-size: 0.8em; color: #aaa; margin-top: 10px; border-top: 1px solid #444; padding-top: 5px; }",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Auto Editor - Reporte de Alineación</h1>"
    ]
    
    # 1. Mostrar Grupos de Retake
    html.append("<h2>Grupos de Tomas (Retakes)</h2>")
    if not results["retake_groups"]:
        html.append("<p>No se detectaron repeticiones.</p>")
        
    for idx, group in enumerate(results["retake_groups"]):
        html.append("<div class='card'>")
        html.append(f"<div class='title'>Oración del Guion: \"{group['script_sentence']}\"</div>")
        html.append(f"<p>Tomas detectadas: {len(group['takes'])}</p>")
        
        for t_idx, take in enumerate(group['takes']):
            is_best = (take == group["best_take"])
            b_cls = "highlight" if is_best else "highlight-bad"
            lbl = "(ELEGIDA)" if is_best else "(DESCARTADA)"
            
            before, hl, after = get_context_words(transcript_words, take["start_idx"], take["end_idx"])
            
            html.append(f"<div style='margin-bottom: 10px;'>")
            html.append(f"<strong>Toma {t_idx+1} {lbl}:</strong><br>")
            html.append(f"<span class='context'>...{before} </span>")
            html.append(f"<span class='{b_cls}'>{hl}</span>")
            html.append(f"<span class='context'> {after}...</span>")
            html.append(f"<div class='meta'>Score: {take['score']:.1f} | Start: {take.get('start_time', 0):.2f}s | End: {take.get('end_time', 0):.2f}s</div>")
            html.append("</div>")
            
        html.append("</div>")
        
    # 2. Improvisaciones (Fuera de Guion)
    html.append("<h2>Fuera de Guion (Improvisaciones / Conservadas)</h2>")
    if not results["unmatched_segments"]:
        html.append("<p>No hay texto fuera de guion.</p>")
        
    for seg in results["unmatched_segments"]:
        if seg.get("is_crumb_merged"):
            continue
            
        html.append("<div class='card'>")
        before, hl, after = get_context_words(transcript_words, seg["start_idx"], seg["end_idx"])
        html.append(f"<span class='context'>...{before} </span>")
        html.append(f"<span class='highlight-improv'>{hl}</span>")
        html.append(f"<span class='context'> {after}...</span>")
        html.append(f"<div class='meta'>Start: {seg.get('start_time', 0):.2f}s | End: {seg.get('end_time', 0):.2f}s</div>")
        html.append("</div>")
        
    # 2.5 Posibles Duplicados (Huérfanos que parecen retakes)
    html.append("<h2>POSIBLE DUPLICADO — REVISAR</h2>")
    if not results.get("possible_duplicates"):
        html.append("<p>No se encontraron duplicados huérfanos.</p>")
        
    for dup in results.get("possible_duplicates", []):
        html.append("<div class='card'>")
        action = dup.get("duplicate_action", "DECISIÓN MANUAL")
        if action == "CORTAR":
            html.append(f"<div class='title' style='color: #dc3545;'>Recomendación: {action} (Toma anterior)</div>")
            hl_class = "highlight-bad"
        else:
            html.append(f"<div class='title' style='color: #ffc107;'>Recomendación: {action} (Toma posterior/Incierta)</div>")
            hl_class = "highlight-improv"
            
        before, hl, after = get_context_words(transcript_words, dup["start_idx"], dup["end_idx"])
        html.append(f"<span class='context'>...{before} </span>")
        html.append(f"<span class='{hl_class}'>{hl}</span>")
        html.append(f"<span class='context'> {after}...</span>")
        html.append(f"<div class='meta'>Start: {dup.get('start_time', 0):.2f}s | End: {dup.get('end_time', 0):.2f}s</div>")
        html.append("</div>")
        
    # 3. Oraciones Saltadas
    html.append("<h2>Oraciones del Guion NO encontradas</h2>")
    if not results["skipped_sentences"]:
        html.append("<p>Todo el guion fue encontrado.</p>")
    else:
        html.append("<ul>")
        for s in results["skipped_sentences"]:
            html.append(f"<li style='color: #dc3545;'>{s}</li>")
        html.append("</ul>")
        
    html.append("</body></html>")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))
        
    return output_path
