import tempfile,unittest
from pathlib import Path
from legend_core.tlang import parse_document,validate_template,TemplateError,execute
from legend_core.engine import compose
from legend_core.store import TemplateStore

ROOT=Path(__file__).resolve().parents[1]
SEEDS=parse_document((ROOT/"templates/library/seed.tlang").read_text(encoding="utf-8"))

class LegendTests(unittest.TestCase):
    def test_all_twenty_seed_templates_execute(self):
        self.assertEqual(len(SEEDS),20)
        for t in SEEDS:
            validate_template(t)
            for case in t["tests"]:
                result,_=execute(t,case["input"])
                self.assertEqual(result,case["expect"])
    def test_composer_returns_verified_math_result(self):
        result=compose(SEEDS,task="please add these",inputs={"a":19,"b":23})
        self.assertEqual(result["status"],"verified")
        self.assertEqual(result["answer"],42)
        self.assertTrue(result["proof"])
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
