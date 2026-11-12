"""The pack as a whole: structure, safety rules, and one run through every step."""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import _synth as S
from _load import ROOT, load

SKILLS = ["binance-scanner", "binance-analyst", "binance-news", "binance-budget", "binance-risk",
          "binance-ticket", "binance-alerts", "binance-rebalance", "binance-journal"]
SCRIPTS = {"binance-scanner": "scan", "binance-analyst": "analyze", "binance-news": "news", "binance-budget": "size",
           "binance-risk": "check", "binance-ticket": "ticket", "binance-alerts": "alerts",
           "binance-rebalance": "rebalance", "binance-journal": "journal"}
# skills that can lead to an order carry the safety block
ORDER_PATH = ["binance-scanner", "binance-budget", "binance-risk", "binance-ticket", "binance-rebalance", "binance-alerts"]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def py_files():
    for s in SKILLS:
        yield os.path.join(ROOT, s, SCRIPTS[s] + ".py")


class Structure(unittest.TestCase):
    def test_nine_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_frontmatter(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 300, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 35, s)
            self.assertIn("${CLAUDE_SKILL_DIR}", text, s)

    def test_every_skill_md_names_its_script_and_the_script_exists(self):
        for s in SKILLS:
            script = SCRIPTS[s] + ".py"
            self.assertTrue(os.path.isfile(os.path.join(ROOT, s, script)), s)
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn(script, text, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("yes", low, s)

    def test_manifests_and_readme(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            self.assertEqual(json.load(fh)["name"], "binance-trader-skill")
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            self.assertEqual(json.load(fh)["plugins"][0]["name"], "binance-trader-skill")
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)
        with open(os.path.join(ROOT, "binance-budget", "config.example.json")) as fh:
            example = json.load(fh)
        self.assertIn("budget_usdt", example)

    def test_config_example_keys_are_all_known_to_some_script(self):
        with open(os.path.join(ROOT, "binance-budget", "config.example.json")) as fh:
            keys = [k for k in json.load(fh) if not k.startswith("_")]
        blob = ""
        for p in py_files():
            with open(p, encoding="utf-8") as fh:
                blob += fh.read()
        for k in keys:
            self.assertIn('"%s"' % k, blob, "config key %s is not used by any script" % k)

    def test_each_script_runs_with_help(self):
        for p in py_files():
            r = subprocess.run([sys.executable, "-I", p, "--help"], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, p + r.stderr)


class Safety(unittest.TestCase):
    """The pack never signs a request, never uses a key, never calls the order endpoints."""

    def test_no_signing_no_keys_no_order_endpoints(self):
        forbidden = [r"hmac", r"X-MBX-APIKEY", r"signature", r"api_?secret", r"secret_?key", r"/api/v3/order", r"/sapi/",
                     r"/fapi/", r"/dapi/", r"newOrder", r"/withdraw", r"BINANCE_API"]
        for p in py_files():
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            for pat in forbidden:
                hits = [ln for ln in text.splitlines() if re.search(pat, ln, re.I)]
                self.assertEqual(hits, [], "%s: %s" % (os.path.basename(p), hits))

    def test_only_the_alerts_script_ever_posts(self):
        for p in py_files():
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            posts = "data=" in text or "method=" in text
            self.assertEqual(posts, os.path.basename(p) == "alerts.py", p)

    def test_network_hosts_are_only_public_data_and_telegram(self):
        allowed = ("data-api.binance.vision", "api.binance.com", "api.telegram.org", "www.binance.com", "www.coindesk.com",
                   "cointelegraph.com", "decrypt.co", "www.w3.org")
        for p in py_files():
            with open(p, encoding="utf-8") as fh:
                for host in re.findall(r"https?://([A-Za-z0-9.-]+)", fh.read()):
                    self.assertIn(host, allowed, "%s mentions %s" % (os.path.basename(p), host))

    def test_the_skill_files_tell_claude_never_to_ask_for_keys(self):
        _, text = frontmatter(os.path.join(ROOT, "binance-ticket", "SKILL.md"))
        self.assertIn("Never ask the user for an API key", text)


class Pipeline(unittest.TestCase):
    """scanner -> budget -> risk -> ticket -> journal -> alerts, sharing one data folder."""

    def test_full_run(self):
        scan = load("binance-scanner", "scan")
        size = load("binance-budget", "size")
        check = load("binance-risk", "check")
        ticket = load("binance-ticket", "ticket")
        journal = load("binance-journal", "journal")
        alerts = load("binance-alerts", "alerts")
        api = S.fake_api(S.universe())
        with tempfile.TemporaryDirectory() as tmp:
            for m in (scan, size, check, ticket, journal, alerts):
                m.DATA_DIR = tmp
            scan.now_ms = lambda: S.NOW
            for m in (scan, size, check, alerts):
                m.fetch_json = api
            with open(os.path.join(tmp, "config.json"), "w") as fh:
                json.dump({"budget_usdt": 100, "max_position_pct": 40}, fh)

            def run(mod, *argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    mod.main(list(argv))
                return out.getvalue()

            out = run(scan, "--write", "--pause", "0")
            self.assertIn("UPUSDT", out)
            out = run(size, "--write")
            self.assertIn("UPUSDT", out)
            orders = json.load(open(os.path.join(tmp, "orders.json")))["orders"]
            self.assertEqual(len(orders), 1)
            self.assertLessEqual(orders[0]["risk_pct"], 1.0 + 1e-9)

            out = run(check, "--live")
            self.assertIn("PASS", out, out)
            self.assertIn("NOT READY", run(ticket, "show"))
            run(ticket, "approve", "UPUSDT", "--confirm", "yes UPUSDT")
            self.assertIn("READY", run(ticket, "show"))
            tk = run(ticket, "render", "UPUSDT")
            self.assertIn("OCO", tk)

            o = orders[0]
            placed = run(ticket, "placed", "UPUSDT", "--price", str(o["price"]), "--qty", str(o["quantity"]))
            self.assertIn("journal.py open", placed)
            run(journal, "open", "UPUSDT", "--price", str(o["price"]), "--qty", str(o["quantity"]),
                "--stop", str(o["stop_price"]), "--target", str(o["take_profit"]))
            self.assertIn("created 2 alerts", run(alerts, "from-journal"))

            # the open position now counts: sizing the same candidate again skips it, and risk blocks a re-run
            again = run(size)
            self.assertIn("already an open position", again)

            run(journal, "close", "UPUSDT", "--price", str(o["take_profit"]))
            self.assertIn("1 closed", run(journal, "today"))


if __name__ == "__main__":
    unittest.main()
