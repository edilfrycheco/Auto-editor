import re
from rapidfuzz import fuzz

class AlignmentEngine:
    def __init__(self, config=None):
        self.config = config or {}
        align_conf = self.config.get("alignment", {})
        if "match_partial" not in align_conf or "partial_min_coverage" not in align_conf:
            raise ValueError("Las claves 'match_partial' y 'partial_min_coverage' deben estar en la sección 'alignment' del config.yaml.")
            
        self.threshold = align_conf.get("tolerance_threshold", 65)
        self.retake_window = align_conf.get("retake_window_seconds", 120)
        self.match_complete = align_conf.get("match_complete", 75)
        self.match_partial = align_conf.get("match_partial", 55)
        self.partial_min_coverage = align_conf.get("partial_min_coverage", 0.4)
        
        from core.llm_judge import LLMJudge
        self.llm_judge = LLMJudge(self.config)
        self.max_silence_s = self.config.get("llm_judge", {}).get("max_internal_silence_ms", 1200) / 1000.0

    def _ask_ollama(self, text, script_sentences, model_name):
        import requests
        import json
        import re
        
        prompt = f"El texto extraído es: '{text}'. ¿Este texto expresa la misma idea que alguna de estas oraciones del guion? Responde SOLO el número de la oración, o 'NINGUNA'. Evita cualquier otra explicación.\nOraciones:\n"
        for i, s in enumerate(script_sentences):
            prompt += f"{i}. {s}\n"
            
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 10
            }
        }
        
        try:
            # timeout corto para no bloquear la app si Ollama no está
            response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=3)
            if response.status_code == 200:
                data = response.json()
                res = data.get("response", "").strip()
                res_clean = re.sub(r'<think>.*?</think>', '', res, flags=re.DOTALL).strip()
                
                # Check if the clean response contains a valid index
                if res_clean.isdigit():
                    idx = int(res_clean)
                    if 0 <= idx < len(script_sentences):
                        print(f"[LLM LOG] Match semántico encontrado: '{text}' -> Oración {idx}")
                        return idx
                        
                print(f"[LLM LOG] Respuesta fallida/fuera de rango/NINGUNA: '{res}'")
            return None
        except Exception as e:
            print(f"[LLM LOG] Excepción llamando a Ollama: {e}")
            return None

    def normalize(self, text):
        import unicodedata
        text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        return text.strip()

    def split_sentences(self, text):
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]

    def align(self, script: str, transcript_words: list, progress_callback=None):
        script_sentences = self.split_sentences(script)
        norm_words = [self.normalize(w["word"]) for w in transcript_words]
        
        # --- Normalización de Nombres (Pase Híbrido) ---
        import os, yaml
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
        nombres_rules = []
        try:
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f)
                nombres_file = cfg.get("paths", {}).get("nombres_file", "nombres.txt")
                fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), nombres_file)
                if os.path.exists(fpath):
                    with open(fpath, "r") as ff:
                        for line in ff:
                            line = line.strip()
                            if not line: continue
                            if ":" in line:
                                variant, correct = line.split(":", 1)
                                nombres_rules.append({"type": "exact", "variant": self.normalize(variant), "correct": self.normalize(correct)})
                            else:
                                correct = self.normalize(line)
                                wc = len(correct.split())
                                nombres_rules.append({"type": "fuzzy", "correct": correct, "wc": wc})
        except Exception as e:
            import traceback
            print(f"[ERROR] Cargando nombres_file: {traceback.format_exc()}")
            raise

        for rule in nombres_rules:
            if rule["type"] == "fuzzy":
                wc = rule["wc"]
                correct = rule["correct"]
                correct_words = correct.split()
                for i in range(len(norm_words) - wc + 1):
                    window_str = " ".join(norm_words[i:i+wc])
                    if fuzz.ratio(correct, window_str) >= 75:
                        for j in range(wc):
                            norm_words[i+j] = correct_words[j] if j < len(correct_words) else ""
            elif rule["type"] == "exact":
                variant_words = rule["variant"].split()
                correct_words = rule["correct"].split()
                wc = len(variant_words)
                for i in range(len(norm_words) - wc + 1):
                    window_str = " ".join(norm_words[i:i+wc])
                    if window_str == rule["variant"]:
                        for j in range(wc):
                            norm_words[i+j] = correct_words[j] if j < len(correct_words) else ""

        
        all_candidates = []
        pointer = 0
        lookahead = 400
        
        for s_idx, sentence in enumerate(script_sentences):
            norm_sent = self.normalize(sentence)
            sent_word_count = len(norm_sent.split())
            if sent_word_count == 0:
                continue
                
            search_end = min(pointer + lookahead, len(norm_words))
            sentence_matches = []
            
            for i in range(pointer, search_end):
                for w_size in range(max(1, sent_word_count - 2), sent_word_count + 4):
                    j = min(i + w_size, len(norm_words))
                    if j <= i: continue
                    
                    candidate_str = " ".join(norm_words[i:j])
                    score = fuzz.token_sort_ratio(norm_sent, candidate_str)
                    
                    if score >= self.match_partial:
                        # Penalize very short matches if they aren't perfect
                        if sent_word_count <= 2 and score < self.match_complete:
                            continue
                            
                        sentence_matches.append({
                            "script_sentence": sentence,
                            "s_idx": s_idx,
                            "start_idx": i,
                            "end_idx": j - 1,
                            "start_time": transcript_words[i].get("start", 0),
                            "end_time": transcript_words[j-1].get("end", 0),
                            "score": score,
                            "length": j - i
                        })
            
            if sentence_matches:
                # Filtrar matches con score < match_complete si no están cerca (<30s) de un match fuerte
                strong_matches = [m for m in sentence_matches if m["score"] >= self.match_complete]
                valid_matches = []
                for m in sentence_matches:
                    if m["score"] >= self.match_complete:
                        valid_matches.append(m)
                    elif strong_matches:
                        if any(abs(m["start_time"] - sm["start_time"]) < 30 for sm in strong_matches):
                            valid_matches.append(m)
                            
                if valid_matches:
                    all_candidates.extend(valid_matches)
                    valid_matches.sort(key=lambda x: x["start_idx"])
                    pointer = valid_matches[0]["start_idx"]
            else:
                # Skipped sentence, do not advance pointer so we can keep searching for the next one
                pass

        # Global Greedy Interval Scheduling to resolve overlaps (both intra-sentence and inter-sentence)
        # Sort by score DESCENDING, then length DESCENDING
        all_candidates.sort(key=lambda x: (x["score"], x["length"]), reverse=True)
        
        assigned = [False] * len(norm_words)
        accepted_matches = []
        
        for cand in all_candidates:
            # Check if any word in the candidate is already assigned
            if not any(assigned[cand["start_idx"] : cand["end_idx"] + 1]):
                # Accept candidate
                accepted_matches.append(cand)
                for i in range(cand["start_idx"], cand["end_idx"] + 1):
                    assigned[i] = True

        accepted_matches.sort(key=lambda x: x["start_idx"])
        
        # --- Self-Similarity Engine (Virtual Retakes) ---
        virtual_matches = []
        assigned_self = set()
        n_words = len(norm_words)
        
        if self.config.get("auto_similarity", {}).get("enabled", False):
        
            # Stopwords en español para filtrar semillas ruidosas
            stopwords = {"a", "al", "algo", "algunas", "algunos", "ante", "antes", "como", "con", "contra", "cual", "cuando", "de", "del", "desde", "donde", "durante", "e", "el", "ella", "ellas", "ellos", "en", "entre", "era", "es", "esa", "esas", "ese", "eso", "esos", "esta", "estaba", "estado", "estamos", "estan", "estar", "estas", "este", "esto", "estos", "estoy", "fue", "fueron", "ha", "habia", "han", "has", "hasta", "hay", "he", "hemos", "la", "las", "le", "les", "lo", "los", "me", "mi", "mis", "mucho", "muchos", "muy", "mas", "nada", "ni", "no", "nos", "nosotras", "nosotros", "nuestra", "nuestras", "nuestro", "nuestros", "o", "os", "otra", "otras", "otro", "otros", "para", "pero", "poco", "por", "porque", "que", "quien", "quienes", "se", "sea", "sean", "si", "sido", "sin", "sobre", "sois", "somos", "son", "soy", "su", "sus", "tambien", "tanto", "te", "tiene", "tienen", "tienes", "todo", "todos", "tu", "tus", "un", "una", "uno", "unos", "y", "ya", "yo"}
            def count_content_words(word_list):
                return sum(1 for w in word_list if w and w not in stopwords)
            
            # Helper for safeguards
            word_to_s_idx = {}
            for m in accepted_matches:
                for i in range(m["start_idx"], m["end_idx"] + 1):
                    word_to_s_idx[i] = m["s_idx"]
                
            for i in range(n_words - 4):
                if i in assigned_self: continue
            
                seed1_words = norm_words[i:i+5]
                if count_content_words(seed1_words) < 2:
                    continue
                
                ng1 = " ".join(seed1_words)
                time1 = transcript_words[i].get("start", 0)
            
                group_takes = []
            
                for j in range(i + 5, n_words - 4):
                    if j in assigned_self: continue
                    time2 = transcript_words[j].get("start", 0)
                    if time2 - time1 > 180:
                        break
                    
                    ng2 = " ".join(norm_words[j:j+5])
                    if fuzz.token_sort_ratio(ng1, ng2) >= 85:
                        # Expand the seed
                        k = 5
                        while j + k < n_words and i + k < n_words:
                            exp_ng1 = " ".join(norm_words[i:i+k+1])
                            exp_ng2 = " ".join(norm_words[j:j+k+1])
                            if fuzz.token_sort_ratio(exp_ng1, exp_ng2) >= 80:
                                k += 1
                            else:
                                break
                            
                        # Filter short groups or poor final ratio
                        if k < 8:
                            continue
                        final_ratio = fuzz.token_sort_ratio(" ".join(norm_words[i:i+k]), " ".join(norm_words[j:j+k]))
                        if final_ratio < 85:
                            continue
                            
                        # Check safeguards
                        s1_indices = {word_to_s_idx[x] for x in range(i, i+k) if x in word_to_s_idx}
                        s2_indices = {word_to_s_idx[x] for x in range(j, j+k) if x in word_to_s_idx}
                    
                        # Safeguard 1: Intentional script repetition (different script sentences match)
                        if s1_indices and s2_indices and s1_indices.isdisjoint(s2_indices):
                            continue
                        
                        # Safeguard 2: >120s apart AND >= 3 script sentences between them
                        if time2 - time1 > 120:
                            s_between = {word_to_s_idx[x] for x in range(i+k, j) if x in word_to_s_idx}
                            if len(s_between) >= 3:
                                continue
                            
                        if not group_takes:
                            virtual_sent = "[AUTO] " + " ".join([w["word"] for w in transcript_words[i:i+k]])
                            group_takes.append({
                                "script_sentence": virtual_sent,
                                "s_idx": -1,
                                "start_idx": i,
                                "end_idx": i+k-1,
                                "start_time": transcript_words[i].get("start", 0),
                                "end_time": transcript_words[i+k-1].get("end", 0),
                                "score": final_ratio,
                                "length": k
                            })
                            for x in range(i, i+k): assigned_self.add(x)
                        
                        group_takes.append({
                            "script_sentence": virtual_sent,
                            "s_idx": -1,
                            "start_idx": j,
                            "end_idx": j+k-1,
                            "start_time": transcript_words[j].get("start", 0),
                            "end_time": transcript_words[j+k-1].get("end", 0),
                            "score": final_ratio,
                            "length": k
                        })
                        for x in range(j, j+k): assigned_self.add(x)
                    
                if len(group_takes) > 1:
                    virtual_matches.extend(group_takes)

        # Group into retakes
        raw_retake_groups = self._group_retakes(accepted_matches + virtual_matches)
        
        # Deduplicate overlapping groups
        final_groups = []
        for g in raw_retake_groups:
            overlap = False
            for fg in final_groups:
                if self._groups_overlap(g, fg):
                    fg["takes"].extend(g["takes"])
                    fg["takes"].sort(key=lambda x: x["start_idx"])
                    fg["start_idx"] = min(fg["start_idx"], g["start_idx"])
                    fg["end_idx"] = max(fg["end_idx"], g["end_idx"])
                    fg["start_time"] = min(fg["start_time"], g["start_time"])
                    fg["end_time"] = max(fg["end_time"], g["end_time"])
                    # Prefer script sentence over virtual sentence
                    if fg.get("s_idx", -1) == -1 and g.get("s_idx", -1) != -1:
                        fg["script_sentence"] = g["script_sentence"]
                        fg["s_idx"] = g["s_idx"]
                    overlap = True
                    break
            if not overlap:
                final_groups.append(g)
                
        # Re-evaluate best_take after deduplication
        total_groups = len(final_groups)
        for i, fg in enumerate(final_groups):
            unique_takes = []
            seen = set()
            for t in fg["takes"]:
                bounds = (t["start_idx"], t["end_idx"])
                if bounds not in seen:
                    seen.add(bounds)
                    unique_takes.append(t)
                    
            # 1. Silence Filter
            valid_takes = []
            for t in unique_takes:
                has_long_silence = False
                for idx in range(t["start_idx"], t["end_idx"]):
                    word_a = transcript_words[idx]
                    word_b = transcript_words[idx+1]
                    silence_s = word_b.get("start", 0) - word_a.get("end", 0)
                    if silence_s > self.max_silence_s:
                        has_long_silence = True
                        break
                
                t["has_long_silence"] = has_long_silence
                if not has_long_silence:
                    valid_takes.append(t)
                    
            pool = valid_takes if valid_takes else unique_takes
            fg["takes"] = unique_takes # keep all for UI
            
            # 2. LLM Judge
            llm_choice_idx = None
            explicit_rejection = False
            
            if self.llm_judge.enabled and len(pool) > 0:
                takes_data = []
                for idx_t, t in enumerate(pool):
                    text = " ".join([w["word"] for w in transcript_words[t["start_idx"]:t["end_idx"]+1]])
                    takes_data.append({"id": idx_t, "text": text, "score": t["score"]})
                
                script_sentence = fg.get("script_sentence", "")
                if progress_callback: progress_callback(f"LLM [{i+1}/{total_groups}]: Evaluando toma(s)...")
                best_idx = self.llm_judge.choose_best_take(script_sentence, takes_data)
                
                if best_idx == -1:
                    explicit_rejection = True
                    if progress_callback: progress_callback(f"LLM [{i+1}/{total_groups}]: ¡Rechazó todas las tomas!")
                    print(f"[LLMJudge] Todas las tomas rechazadas para '{script_sentence[:30]}...'")
                elif best_idx is not None:
                    llm_choice_idx = best_idx
                    if progress_callback: progress_callback(f"LLM [{i+1}/{total_groups}]: ¡Resolvió a favor de la toma {best_idx}!")
                    print(f"[LLMJudge] Elegida toma {best_idx} para '{script_sentence[:30]}...'")
                else:
                    if progress_callback: progress_callback(f"LLM [{i+1}/{total_groups}]: LLM falló (Timeout/API). Usando matemáticas.")
                    
            if explicit_rejection:
                fg["best_take"] = None
            elif llm_choice_idx is not None:
                fg["best_take"] = pool[llm_choice_idx]
                fg["best_take"]["chosen_by_llm"] = True
            else:
                fg["best_take"] = self._score_best_take(pool) if pool else None
            
        retake_groups = final_groups
        
        # Determine skipped sentences
        matched_s_indices = {m["s_idx"] for m in accepted_matches}
        skipped_sentences = [s for i, s in enumerate(script_sentences) if i not in matched_s_indices]
        
        # Determine unmatched segments (improvisations)
        unmatched_segments = []
        current_unmatched_start = None
        
        for i in range(len(norm_words)):
            if not assigned[i]:
                if current_unmatched_start is None:
                    current_unmatched_start = i
            else:
                if current_unmatched_start is not None:
                    unmatched_segments.append({
                        "start_idx": current_unmatched_start,
                        "end_idx": i - 1,
                        "start_time": transcript_words[current_unmatched_start].get("start", 0),
                        "end_time": transcript_words[i-1].get("end", 0)
                    })
                    current_unmatched_start = None
                    
        if current_unmatched_start is not None:
            unmatched_segments.append({
                "start_idx": current_unmatched_start,
                "end_idx": len(norm_words) - 1,
                "start_time": transcript_words[current_unmatched_start].get("start", 0),
                "end_time": transcript_words[-1].get("end", 0)
            })

        # --- Filtro de Migajas y Charla de Set ---
        status = ["UNMATCHED"] * len(norm_words)
        for group in retake_groups:
            for t in group["takes"]:
                s = "KEPT" if t == group["best_take"] else "DISCARDED"
                for i in range(t["start_idx"], t["end_idx"] + 1):
                    status[i] = s

        import os
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
        frases = []
        try:
            import yaml
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f)
                frases_file = cfg.get("paths", {}).get("frases_correccion_file", "frases_correccion.txt")
                fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), frases_file)
                if os.path.exists(fpath):
                    with open(fpath, "r") as ff:
                        frases = [self.normalize(line) for line in ff if line.strip()]
        except Exception as e:
            import traceback
            print(f"[ERROR] Cargando frases_correccion_file: {traceback.format_exc()}")
            raise

        filtered_unmatched = []
        
        # Obtener tiempos de todos los DISCARDED para chequeo de +-3s
        discarded_intervals = []
        for group in retake_groups:
            for t in group["takes"]:
                if t != group["best_take"]:
                    discarded_intervals.append((t["start_time"], t["end_time"]))
                    
        def is_adjacent_to_discarded(start_t, end_t):
            for (d_start, d_end) in discarded_intervals:
                # Si el intervalo descartado está a menos de 3.0s de distancia
                if abs(start_t - d_end) <= 3.0 or abs(d_start - end_t) <= 3.0:
                    return True
            return False

        for u in unmatched_segments:
            word_count = u["end_idx"] - u["start_idx"] + 1
            duration = u["end_time"] - u["start_time"]
            text_norm = " ".join([self.normalize(w["word"]) for w in transcript_words[u["start_idx"]:u["end_idx"]+1]])
            
            is_set_chat = any(f in text_norm for f in frases if f)
            is_crumb = word_count < 4 or duration < 2.0
            
            # Tolerancia de tiempo para "adyacente a descartado"
            adjacent_to_discarded_time = is_adjacent_to_discarded(u["start_time"], u["end_time"])
            
            # --- LOGGING SOLICITADO ---
            # Si el texto es sustancial, logueamos qué pasa
            if is_set_chat:
                found_signals = [f for f in frases if f and f in text_norm]
                print(f"[SET CHAT LOG] Seg {u['start_time']:.1f}-{u['end_time']:.1f}s | Texto: '{text_norm}' | Señal: {found_signals} | Ady DISCARDED: {adjacent_to_discarded_time}")
            else:
                # Ocasionalmente logueamos si contiene una palabra clave sospechosa para debug
                if "accion" in text_norm or "ok" in text_norm or "equivoc" in text_norm:
                    print(f"[SET CHAT LOG - FALSO POSITIVO] Seg {u['start_time']:.1f}-{u['end_time']:.1f}s | Texto: '{text_norm}' | is_set_chat=False")

            prev_status = status[u["start_idx"] - 1] if u["start_idx"] > 0 else "UNMATCHED"
            next_status = status[u["end_idx"] + 1] if u["end_idx"] < len(norm_words) - 1 else "UNMATCHED"
            adjacent_to_kept = (prev_status == "KEPT" or next_status == "KEPT")
            
            if is_set_chat and adjacent_to_discarded_time:
                continue
                
            if is_crumb and adjacent_to_discarded_time:
                continue
                
            if is_crumb and adjacent_to_kept:
                u["is_crumb_merged"] = True
                filtered_unmatched.append(u)
                continue
                
            filtered_unmatched.append(u)

        # --- Pase Final: Deduplicación de Huérfanos ---
        final_unmatched = []
        possible_duplicates = []
        
        # Pre-computar textos de best_takes para deduplicación
        best_takes_text = []
        for group in retake_groups:
            if group.get("best_take"):
                t = group["best_take"]
                txt = " ".join([self.normalize(w["word"]) for w in transcript_words[t["start_idx"]:t["end_idx"]+1]])
                best_takes_text.append({"text": txt, "score": t["score"], "start_time": t["start_time"]})
                
        script_sentences_norm = [self.normalize(s) for s in script_sentences]

        for u in filtered_unmatched:
            if u.get("is_crumb_merged"):
                final_unmatched.append(u)
                continue
                
            u_text = " ".join([self.normalize(w["word"]) for w in transcript_words[u["start_idx"]:u["end_idx"]+1]])
            
            is_duplicate = False
            rec_action = "DECISIÓN MANUAL"
            
            # Comparar contra best_takes en +-90s
            for bt in best_takes_text:
                if abs(bt["start_time"] - u["start_time"]) <= 90.0:
                    if fuzz.partial_ratio(u_text, bt["text"]) >= 70:
                        is_duplicate = True
                        if u["start_time"] < bt["start_time"]:
                            rec_action = "CORTAR"  # Es anterior al elegido -> Cortar
                        else:
                            rec_action = "DECISIÓN MANUAL" # Posterior -> Revisar manual
                        break
                        
            # Si no, comparar contra guion
            if not is_duplicate:
                for s_norm in script_sentences_norm:
                    if fuzz.partial_ratio(u_text, s_norm) >= 70:
                        is_duplicate = True
                        rec_action = "DECISIÓN MANUAL"
                        break
                        
            # LLM Semántico (solo si no se determinó CORTAR por fuzzy y ollama está activo)
            if self.llm_judge.enabled and rec_action != "CORTAR":
                if progress_callback: progress_callback(f"LLM: Evaluando improvisación/basura...")
                llm_action = self.llm_judge.evaluate_unmatched_segment(u_text)
                if llm_action == "CORTAR":
                    is_duplicate = True
                    rec_action = "CORTAR"
                    print(f"[LLMJudge] Huérfano descartado por IA: '{u_text}'")
                elif llm_action == "CONSERVAR":
                    is_duplicate = False
                    rec_action = "DECISIÓN MANUAL"
                    print(f"[LLMJudge] Huérfano conservado como improvisación: '{u_text}'")
                        
            if is_duplicate:
                u["duplicate_action"] = rec_action
                possible_duplicates.append(u)
                # Si es decisión manual, lo conservamos por defecto en la timeline
                if rec_action == "DECISIÓN MANUAL":
                    final_unmatched.append(u)
            else:
                final_unmatched.append(u)

        return {
            "matches": accepted_matches,
            "retake_groups": retake_groups,
            "skipped_sentences": skipped_sentences,
            "unmatched_segments": final_unmatched,
            "possible_duplicates": possible_duplicates
        }

    def _group_retakes(self, accepted_matches):
        if not accepted_matches: return []
        
        groups_by_sentence = {}
        for m in accepted_matches:
            sent = m["script_sentence"]
            if sent not in groups_by_sentence:
                groups_by_sentence[sent] = []
            groups_by_sentence[sent].append(m)
            
        retake_groups = []
        for sent, takes in groups_by_sentence.items():
            takes.sort(key=lambda x: x["start_idx"])
            best_take = self._score_best_take(takes)
            retake_groups.append({
                "script_sentence": sent,
                "s_idx": takes[0].get("s_idx", -1),
                "takes": takes,
                "best_take": best_take,
                "start_idx": takes[0]["start_idx"],
                "end_idx": takes[-1]["end_idx"],
                "start_time": takes[0]["start_time"],
                "end_time": takes[-1]["end_time"]
            })
            
        retake_groups.sort(key=lambda x: x["start_idx"])
        return retake_groups

    def _groups_overlap(self, g1, g2):
        for t1 in g1["takes"]:
            for t2 in g2["takes"]:
                if max(t1["start_idx"], t2["start_idx"]) <= min(t1["end_idx"], t2["end_idx"]):
                    return True
        return False

    def _score_best_take(self, takes):
        if not takes: return None
        if len(takes) == 1: return takes[0]
        
        highest_score = max(t["score"] for t in takes)
        
        # Evaluamos desde la más tardía hacia la primera
        for t in reversed(takes):
            # Si la toma está completa (>= 80) y no es significativamente peor que la mejor (> 8 puntos)
            if t["score"] >= 80 and (highest_score - t["score"]) <= 8:
                return t
                
        # Fallback: gana el mejor score puro, y en empate la última
        best = None
        best_score = -9999
        for t in takes:
            if t["score"] >= best_score:
                best_score = t["score"]
                best = t
        return best
