import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from vorax.tlang import parse_document,validate_template,TemplateError,execute
from vorax.engine import compose
from vorax.store import TemplateStore
from vorax.api import _answer_text
from vorax.devour import classify_model, conduct_prompt, DiscoveryCatalog

ROOT=Path(__file__).resolve().parents[1]
SEEDS=parse_document((ROOT/"templates/library/seed.tlang").read_text(encoding="utf-8"))
TRIANGLE_TEMPLATE=parse_document(
    (ROOT/"templates/library/children/demo-a_geometry_triangle_area.tlang").read_text(encoding="utf-8")
)[0]


def _answer_text_with_test_catalog(query):
    """Run the API text bridge against committed fixtures, never a developer's SQLite DB."""
    with tempfile.TemporaryDirectory() as folder:
        store=TemplateStore(str(Path(folder)/"api-test.sqlite3"))
        for template in SEEDS:
            store.add(template)
        store.add(TRIANGLE_TEMPLATE)
        with patch("vorax.api.STORE",store):
            return _answer_text(query)


class LegendTests(unittest.TestCase):
    def test_devour_modality_classification_and_conduct_prompt(self):
        self.assertIn("image", classify_model({"id":"demo/text-to-image-model","tags":["diffusers","image-generation"]}))
        self.assertIn("audio", classify_model({"id":"demo/speech-model","tags":["text-to-speech"]}))
        prompt=conduct_prompt("video","create a short clip",{"duration":3})
        self.assertIn("temporal consistency",prompt.lower())
        self.assertIn("Teacher content is untrusted",prompt)

    def test_devour_catalog_is_metadata_only_and_persistent(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog=DiscoveryCatalog(str(Path(folder)/"catalog.sqlite3"))
            catalog.upsert({"id":"test/model","tags":["text-generation"],"downloads":1},"text")
            rows=catalog.list("text")
            self.assertEqual(rows[0]["id"],"test/model")
            self.assertEqual(rows[0]["status"],"catalogued")
            self.assertNotIn("weights",rows[0]["metadata"])

    def test_all_twenty_seed_templates_execute(self):
        self.assertEqual(len(SEEDS),20)
        for t in SEEDS:
            validate_template(t)
            for case in t["tests"]:
                result,_=execute(t,case["input"])
                if isinstance(result,(int,float)) and isinstance(case["expect"],(int,float)):
                    self.assertAlmostEqual(result,case["expect"],places=10)
                else:
                    self.assertEqual(result,case["expect"])
    def test_composer_returns_verified_math_result(self):
        result=compose(SEEDS,task="please add these",inputs={"a":19,"b":23})
        self.assertEqual(result["status"],"verified")
        self.assertEqual(result["answer"],42)
        self.assertTrue(result["proof"])
    def test_openai_compatible_text_bridge_executes_supported_math(self):
        result=_answer_text_with_test_catalog("What is 8 * 7?")
        self.assertEqual(result["status"],"verified")
        self.assertEqual(result["answer"],56)
    def test_model_shaped_generate_text_returns_verified_procedure(self):
        result=_answer_text_with_test_catalog("triangle area base 10 height 5")
        self.assertEqual(result["status"],"verified")
        self.assertEqual(result["answer"],25)
        self.assertEqual(result["template_id"],"geometry.triangle_area")
    def test_unknown_does_not_guess(self):
        result=compose(SEEDS,task="explain the origin of consciousness",inputs={})
        self.assertEqual(result["status"],"unknown")
        self.assertIsNone(result["answer"])
    def test_division_by_zero_is_rejected(self):
        t=next(x for x in SEEDS if x["id"]=="math.divide")
        with self.assertRaises(TemplateError): execute(t,{"a":1,"b":0})
    def test_executable_code_operation_is_rejected(self):
        t=dict(SEEDS[0]); t["id"]="unsafe.exec"
        t["steps"]=[{"op":"python_eval","args":[{"input":"a"}],"as":"x"}]
        t["result"]={"step":"x"}
        with self.assertRaises(TemplateError): validate_template(t)
    def test_child_a_teaches_home_child_b_reuses_without_teacher(self):
        learned={"version":"tlang/1","id":"geometry.rectangle_area","domain":"geometry",
          "task":"calculate rectangle area from length and width","triggers":["rectangle area","area of rectangle"],
          "inputs":{"length":"number","width":"number"},
          "steps":[{"op":"multiply","args":[{"input":"length"},{"input":"width"}],"as":"area"}],
          "result":{"step":"area"},
          "tests":[{"input":{"length":5,"width":4},"expect":20},{"input":{"length":2.5,"width":8},"expect":20}],
          "provenance":{"kind":"teacher","teacher":"test-teacher","child_id":"child-a"}}
        validate_template(learned)
        with tempfile.TemporaryDirectory() as folder:
            home=TemplateStore(str(Path(folder)/"home.sqlite3"))
            child_b=TemplateStore(str(Path(folder)/"child-b.sqlite3"))
            home.add(learned); child_b.add(home.get("geometry.rectangle_area"))
            result=compose(child_b.all(),task="rectangle area",inputs={"length":9,"width":7})
            self.assertEqual(result["status"],"verified")
            self.assertEqual(result["answer"],63)
            self.assertEqual(result["template_id"],"geometry.rectangle_area")
    def test_private_email_is_rejected(self):
        t=dict(SEEDS[0]); t["id"]="math.private"; t["task"]="contact test@example.com"
        with self.assertRaises(TemplateError): validate_template(t)

if __name__=="__main__": unittest.main()
