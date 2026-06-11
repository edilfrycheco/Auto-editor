import json
import logging
import requests

logger = logging.getLogger(__name__)

class LLMJudge:
    def __init__(self, config):
        llm_conf = config.get("llm_judge", {})
        self.enabled = llm_conf.get("enabled", False)
        self.primary_model = llm_conf.get("primary_model", "qwen3:32b")
        self.fallback_model = llm_conf.get("fallback_model", "deepseek-r1:32b")
        self.ollama_url = llm_conf.get("ollama_url", "http://localhost:11434/api/generate")
        
    def query_ollama(self, model, prompt, expect_json=False, timeout=30):
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }
        if expect_json:
            payload["format"] = "json"
            
        try:
            response = requests.post(self.ollama_url, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json().get("response", "")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error querying Ollama ({model}): {e}")
            return None
            
    def choose_best_take(self, target_script, takes_data):
        """
        takes_data is a list of dicts: [{"id": 0, "text": "...", "score": 90}, ...]
        Returns the ID of the best take, or None if it cannot decide.
        """
        if not self.enabled:
            return None
            
        if not takes_data:
            return None
            
        # Fast path if only one take
        if len(takes_data) == 1:
            return takes_data[0]["id"]
            
        takes_text = "\n".join([f"Toma {t['id']}: \"{t['text']}\"" for t in takes_data])
        
        prompt = f"""Eres un juez experto en edición de video.
Tu objetivo es elegir la mejor toma (retake) para un fragmento de guion.
El guion original es: "{target_script}"

Aquí están las opciones que dijo el presentador:
{takes_text}

Evalúa:
1. Fluidez y naturalidad: La toma debe sonar fluida, sin tartamudeos fuertes ni muletillas repetitivas ('eh', 'este').
2. Improvisación vs Errores: ¡OJO! Es TOTALMENTE VÁLIDO que el presentador improvise, omita palabras o exprese la misma idea con otras palabras. Debes CONSERVAR la improvisación natural.
3. Errores fatales (Romper personaje): SOLO debes rechazar una toma si el presentador rompe el personaje, se detiene por error, se ríe de una equivocación, o le habla a la producción (ej. "está muy al paso", "no sé", "vamos de nuevo", "me equivoqué").

Si hay una toma válida (incluso si es improvisada), devuelve su ID.
Si TODAS las tomas tienen errores fatales (rompen el personaje o tienen tartamudeos destructivos), devuelve -1 para rechazar el grupo completo.

Tu respuesta DEBE ser un JSON estricto con el formato:
{{
  "best_take_id": <numero>
}}
Devuelve -1 si todas las tomas tienen errores obvios. No incluyas explicaciones en el JSON."""

        # 1. Primary Model (Fast)
        logger.debug(f"[LLMJudge] Consultando {self.primary_model} para {len(takes_data)} tomas...")
        response = self.query_ollama(self.primary_model, prompt, expect_json=True, timeout=60)
        
        best_id = self._parse_json_response(response)
        
        if best_id is None and self.fallback_model:
            # 2. Escalation to Fallback (Reasoning) ONLY if primary model completely failed to format JSON
            logger.debug(f"[LLMJudge] Qwen falló en el formato. Escalando a {self.fallback_model}...")
            fallback_prompt = prompt.replace("No incluyas explicaciones en el JSON.", "Puedes pensar paso a paso antes de devolver el JSON final.")
            fallback_resp = self.query_ollama(self.fallback_model, fallback_prompt, expect_json=False, timeout=180)
            best_id = self._extract_json_from_text(fallback_resp)
            
        if best_id == -1:
            return -1  # Explicit rejection
            
        if best_id is not None:
            # Verify the ID actually exists in our options
            if any(t["id"] == best_id for t in takes_data):
                return best_id
                
        return None
        
    def evaluate_unmatched_segment(self, text):
        """
        Evaluates an unmatched segment (orphan text) to determine if it's a valid improvisation
        or a speaker mistake/garbage that should be cut.
        """
        if not self.enabled:
            return "DECISIÓN MANUAL"
            
        prompt = f"""Eres un juez experto en edición de video.
El presentador dijo lo siguiente fuera de guion:
"{text}"

Evalúa si esto es:
A) Improvisación válida o continuación natural del tema.
B) Error del presentador: comentarios a producción, muletillas excesivas, equivocaciones (ej. "está muy al paso", "no sé", "me equivoqué", "vamos de nuevo"), o fragmentos sin sentido.

Si es un error o basura (B), debes devolver "CORTAR".
Si es una improvisación válida (A), debes devolver "CONSERVAR".

Tu respuesta DEBE ser un JSON estricto con el formato:
{{
  "action": "CORTAR" | "CONSERVAR"
}}
No incluyas explicaciones en el JSON."""

        logger.debug(f"[LLMJudge] Evaluando segmento huérfano: '{text[:30]}...'")
        response = self.query_ollama(self.primary_model, prompt, expect_json=True, timeout=30)
        
        if not response:
            return "DECISIÓN MANUAL"
            
        try:
            data = json.loads(response)
            return data.get("action", "DECISIÓN MANUAL")
        except:
            return "DECISIÓN MANUAL"
        
    def _parse_json_response(self, response_text):
        if not response_text: return None
        try:
            data = json.loads(response_text)
            return data.get("best_take_id", None)
        except json.JSONDecodeError:
            return self._extract_json_from_text(response_text)
            
    def _extract_json_from_text(self, text):
        if not text: return None
        import re
        # Try to find JSON block
        match = re.search(r'\{[^{}]*\"best_take_id\"\s*:\s*(-?\d+)[^{}]*\}', text)
        if match:
            return int(match.group(1))
        return None
