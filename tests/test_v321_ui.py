"""v3.2.1의 작은 화면용 이동과 TEST 분리 동작을 확인한다."""
from pathlib import Path
import subprocess
import sys
import unittest

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "streamlit_app.py"


class V321UiTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["data_mode"], "TEST")
        return app

    def test_mobile_cart_navigation_preserves_search_and_quantities(self):
        app = self.app()
        app.number_input(key="qty_TEST-P003").set_value(3).run()
        app.text_input(key="search_keyword").set_value("커피")
        app.button(key="search_button").click().run()
        app.button(key="mobile_cart_view").click().run()

        self.assertEqual(app.session_state["workspace_view"], "장바구니")
        self.assertEqual(app.number_input(key="qty_TEST-P003").value, 3)
        self.assertEqual(app.session_state["search_keyword"], "커피")
        self.assertTrue(app.session_state["search_result"].candidates)

        app.button_group(key="workspace_view").set_value("상품 찾기").run()
        self.assertEqual(app.text_input(key="search_keyword").value, "커피")
        self.assertEqual(app.number_input(key="qty_TEST-P003").value, 3)
        self.assertFalse(app.exception)

    def test_mobile_compare_keeps_default_test_recommendation(self):
        app = self.app()
        app.button(key="mobile_lookup_button").click().run()

        self.assertEqual(app.session_state["workspace_view"], "추천·경로")
        plan = app.session_state["last_summary"]["최저 결제액"]
        self.assertEqual(plan["total"], 4100)
        self.assertEqual(len(plan["route"]), 2)
        self.assertAlmostEqual(plan["distance"], 0.36, delta=0.02)
        self.assertEqual(app.session_state["last_distance_basis"], "도보 경로")
        self.assertTrue(any(item.value == "방문 경로" for item in app.subheader))
        self.assertFalse(app.exception)

    def test_empty_cart_disables_both_compare_buttons_and_can_recover(self):
        app = self.app()
        app.button(key="clear_cart").click().run()
        self.assertTrue(app.button(key="lookup_button").disabled)
        self.assertTrue(app.button(key="mobile_lookup_button").disabled)

        available = next(item for item in app.button if (item.key or "").startswith("catalog_add_"))
        available.click().run()
        self.assertFalse(app.button(key="lookup_button").disabled)
        self.assertFalse(app.button(key="mobile_lookup_button").disabled)
        self.assertFalse(app.exception)

    def test_no_brands_disables_both_compare_buttons_and_can_recover(self):
        app = self.app()
        app.multiselect(key="brands").set_value([]).run()
        self.assertTrue(app.button(key="lookup_button").disabled)
        self.assertTrue(app.button(key="mobile_lookup_button").disabled)

        app.multiselect(key="brands").set_value(["CU"]).run()
        self.assertFalse(app.button(key="lookup_button").disabled)
        self.assertFalse(app.button(key="mobile_lookup_button").disabled)
        self.assertFalse(app.exception)

    def test_fresh_test_start_does_not_import_public_modules_or_use_network(self):
        # A fresh process avoids contamination by separately run parser tests.
        code = """
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / 'scripts'))
from run_tests import socket, guard, original_connect, original_connect_ex, attempts
from streamlit.testing.v1 import AppTest
with patch.object(socket.socket, 'connect', new=guard(original_connect)), patch.object(socket.socket, 'connect_ex', new=guard(original_connect_ex)):
    app = AppTest.from_file(str(Path.cwd() / 'streamlit_app.py'), default_timeout=30).run()
assert not app.exception, list(app.exception)
assert app.session_state['data_mode'] == 'TEST'
assert app.session_state['last_summary']['최저 결제액']['total'] == 4100
assert 'planner.public_data' not in sys.modules
assert 'planner.public_data_ui' not in sys.modules
assert not attempts, attempts
print('TEST startup isolated; external connections: 0')
"""
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("external connections: 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
