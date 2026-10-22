import re
import unittest
from pathlib import Path

PRESENTATION = Path(__file__).resolve().parents[1] / "web" / "presentation"
ASSETS = PRESENTATION.parent / "assets"


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.slides = (PRESENTATION / "slides.js").read_text(encoding="utf-8")

    def test_planned_time_fills_the_five_minute_talk(self):
        seconds = [int(value) for value in re.findall(r"seconds: (\d+)", self.slides)]
        self.assertEqual(len(seconds), 10)
        self.assertEqual(sum(seconds), 416)

    def test_demo_steps_fill_the_demo_slide_time(self):
        demo = self.slides[self.slides.index("id: 'demo'"):self.slides.index("id: 'sim'")]
        steps = [int(value) for value in re.findall(r"sec: (\d+)", demo)]
        self.assertEqual(len(steps), 9)
        self.assertEqual(sum(steps), int(re.search(r"seconds: (\d+)", demo).group(1)))
        self.assertEqual(demo.count("say: "), len(steps))
        self.assertEqual(demo.count("action: "), len(steps))

    def test_every_slide_has_speaker_notes(self):
        self.assertEqual(self.slides.count("notes: ["), self.slides.count("seconds: "))

    def test_referenced_assets_exist(self):
        paths = re.findall(r"\$\{A\}([^\"'`]+)", self.slides)
        self.assertTrue(paths)
        for path in paths:
            self.assertTrue((ASSETS / path).is_file(), path)

    def test_pages_load_the_shared_scripts(self):
        for page in ("index.html", "presenter.html"):
            html = (PRESENTATION / page).read_text(encoding="utf-8")
            for script in ("slides.js", "sync.js", "deck.js"):
                self.assertIn(f'src="{script}', html, (page, script))
        self.assertTrue((PRESENTATION / "main.js").is_file())
        self.assertTrue((PRESENTATION / "presenter.js").is_file())


if __name__ == "__main__":
    unittest.main()
