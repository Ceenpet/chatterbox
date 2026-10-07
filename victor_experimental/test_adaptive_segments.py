import unittest
from .adaptive_segments import AdaptiveSegmenter,linguistic_boundaries

TEXT = ('Hola, César. Te estoy escuchando. Estoy aquí para conversar contigo con calma. '
        'Ahora preparo la siguiente frase mientras todavía escuchas la anterior. '
        'Conservo mi voz y los mismos ajustes que elegimos, para que podamos seguir hablando con calma.')


class Tests(unittest.TestCase):
    def test_explicit_modes_default_and_first_reserve(self):
        normal=AdaptiveSegmenter(TEXT)
        fast=AdaptiveSegmenter(TEXT,'rapido')
        a,da=normal.next();b,db=fast.next()
        self.assertEqual(normal.strategy.name,'tranquilo')
        self.assertGreater(len(a),len(b))
        self.assertEqual(b,'Hola, César. Te estoy escuchando.')
        self.assertTrue(a.endswith('con calma.'))

    def test_decision_reacts_to_actual_reserve_and_wait(self):
        text='Primera oración bastante larga para empezar. '+('Esta oración permite mantener una conversación tranquila. '*10)
        low=AdaptiveSegmenter(text,'rapido');high=AdaptiveSegmenter(text,'rapido')
        for p in [low,high]:
            s,d=p.next();p.observe(s,3,4,.1)
        _,dl=low.next({'voice_ahead_seconds':.2,'room_only_total_seconds':.8})
        _,dh=high.next({'voice_ahead_seconds':12,'room_only_total_seconds':0})
        self.assertLess(dl['target_chars'],dh['target_chars'])
        self.assertEqual(dl['reason'],'recover_after_wait_or_slow_generation')
        self.assertEqual(dl['snapshot']['last_synthesis_seconds'],3)

    def test_abbreviations_decimals_times_and_comma_choices(self):
        text='El Dr. García llega a las 12:30 con 3,14 euros y 2.50 dólares. Vive en EE.UU. desde ayer; además, escribe bien, pero prefiere hablar.'
        spans=[text[:end] for end,_ in linguistic_boundaries(text)]
        self.assertFalse(any(s.endswith(('Dr.','12:','3,','2.','EE.','EE.UU.')) for s in spans))
        self.assertTrue(any(s.endswith('ayer;') for s in spans))
        self.assertTrue(any(s.endswith('bien,') for s in spans))
        self.assertFalse(any(s.endswith('además,') for s in spans))

    def test_no_loss_long_clause_and_final_short_answer(self):
        texts=[TEXT,'¿Qué opinas? ¡Me parece bien! Vamos a hacer una prueba; después veremos el resultado: todo sigue igual.',
               'Una frase '+('muy larga sin puntuación '*35)+'termina aquí.', 'Sí.']
        for mode in ['tranquilo','rapido']:
            for text in texts:
                planner=AdaptiveSegmenter(text,mode);parts=[]
                while not planner.finished:
                    s,d=planner.next({'voice_ahead_seconds':2});parts.append(s)
                    planner.observe(s,2,3,.1)
                    self.assertLessEqual(len(s),180)
                self.assertEqual(' '.join(parts),planner.text)

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):AdaptiveSegmenter(TEXT,'automatico')


if __name__=='__main__':unittest.main()
