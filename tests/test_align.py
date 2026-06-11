import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.align import AlignmentEngine

def make_word(word, start, end):
    return {"word": word, "start": start, "end": end, "score": 0.9}

class TestAlignmentEngine(unittest.TestCase):
    def setUp(self):
        # Configuración básica
        self.config = {
            "alignment": {
                "tolerance_threshold": 65,
                "retake_window_seconds": 120,
                "match_complete": 75,
                "match_partial": 55,
                "partial_min_coverage": 0.4
            }
        }
        self.engine = AlignmentEngine(self.config)

    def test_repeated_phrase_falso_comienzo(self):
        """Una frase repetida 3 veces con falso comienzo"""
        script = "Esta es la introducción oficial del video."
        # "Esta es la intro... Esta es la introducción... Esta es la introducción oficial del video."
        transcript_words = [
            make_word("Esta", 1.0, 1.2), make_word("es", 1.2, 1.4), make_word("la", 1.4, 1.5), make_word("intro", 1.5, 1.8),
            make_word("Esta", 3.0, 3.2), make_word("es", 3.2, 3.4), make_word("la", 3.4, 3.5), make_word("introducción", 3.5, 4.0),
            make_word("Esta", 5.0, 5.2), make_word("es", 5.2, 5.4), make_word("la", 5.4, 5.5), make_word("introducción", 5.5, 6.0), 
            make_word("oficial", 6.0, 6.5), make_word("del", 6.5, 6.7), make_word("video", 6.7, 7.0)
        ]
        
        results = self.engine.align(script, transcript_words)
        
        # Deben detectarse como un grupo de retake
        retakes = results.get("retake_groups", [])
        self.assertEqual(len(retakes), 1)
        
        # La toma elegida (best_take) debe ser la última, que va del índice 8 al 14
        group = retakes[0]
        self.assertEqual(group["best_take"]["start_idx"], 8)
        self.assertEqual(group["best_take"]["end_idx"], 14)

    def test_muletilla_aislada(self):
        """Una muletilla aislada (no se maneja directamente en aligner, pero verificamos que no rompe el texto)"""
        script = "Hoy vamos a hablar de finanzas."
        # "Hoy vamos eh a hablar de finanzas."
        transcript_words = [
            make_word("Hoy", 1.0, 1.2), make_word("vamos", 1.2, 1.5), 
            make_word("eh", 2.0, 2.5), 
            make_word("a", 3.0, 3.1), make_word("hablar", 3.1, 3.5), make_word("de", 3.5, 3.6), make_word("finanzas", 3.6, 4.0)
        ]
        
        results = self.engine.align(script, transcript_words)
        matches = results.get("matches", [])
        self.assertEqual(len(matches), 1)
        # Debe cubrir todo desde "Hoy" hasta "finanzas"
        self.assertEqual(matches[0]["start_idx"], 0)
        self.assertEqual(matches[0]["end_idx"], 6)

    def test_oracion_saltada(self):
        """Una oración del guion nunca dicha (El motor avanza y no se atasca)"""
        script = "Primera frase. Segunda frase saltada. Tercera frase."
        transcript_words = [
            make_word("Primera", 1.0, 1.5), make_word("frase", 1.5, 2.0),
            make_word("Tercera", 3.0, 3.5), make_word("frase", 3.5, 4.0)
        ]
        results = self.engine.align(script, transcript_words)
        
        self.assertEqual(len(results["matches"]), 2)
        # La primera frase fue detectada
        self.assertEqual(results["matches"][0]["script_sentence"], "Primera frase.")
        # La tercera frase fue detectada
        self.assertEqual(results["matches"][1]["script_sentence"], "Tercera frase.")
        
        # La segunda debe estar en unclassified o skipped
        skipped = results.get("skipped_sentences", [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0], "Segunda frase saltada.")

    def test_improvisacion_fuera_de_guion(self):
        """Una improvisación fuera de guion"""
        script = "Inicio oficial. Fin oficial."
        transcript_words = [
            make_word("Inicio", 1.0, 1.5), make_word("oficial", 1.5, 2.0),
            make_word("Esto", 2.5, 2.7), make_word("es", 2.7, 2.9), make_word("extra", 2.9, 3.5),
            make_word("Fin", 4.0, 4.5), make_word("oficial", 4.5, 5.0)
        ]
        results = self.engine.align(script, transcript_words)
        
        # Debe haber un rango unclassified entre las palabras 2 y 4 (Esto es extra)
        unmatched = results.get("unmatched_segments", [])
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["start_idx"], 2)
        self.assertEqual(unmatched[0]["end_idx"], 4)

    def test_orden_invertido(self):
        """Dos oraciones en orden invertido en el guion vs el audio."""
        script = "Oracion A. Oracion B."
        # Audio: B luego A
        transcript_words = [
            make_word("Oracion", 1.0, 1.5), make_word("B", 1.5, 2.0),
            make_word("Oracion", 3.0, 3.5), make_word("A", 3.5, 4.0)
        ]
        results = self.engine.align(script, transcript_words)
        # Dependiendo de la implementación, puede detectar ambas si buscamos localmente, 
        # o saltarse A si el puntero monótono avanzó muy rápido.
        # Pero asumiendo una búsqueda de ventana amplia, debería encontrar ambas.
        self.assertEqual(len(results["matches"]), 2)

if __name__ == '__main__':
    unittest.main()
