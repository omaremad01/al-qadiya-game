"""اختبار سريع من غير إنترنت ولا مفتاح: بيستبدل Groq بردود مزيفة ويجرّب اللعبة كلها.
التشغيل:  python -m tests.smoke_test"""
import json
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["CASE_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from backend import llm  # noqa: E402


def fake_case():
    sus = [{"id": f"s{i}", "name": f"شخص {i}", "age": 30 + i, "job": "شغلانة", "relation": "صاحب",
            "public_bio": "تعريف", "personality": "هادي", "speech_style": "عادي", "emoji": "🧔",
            "alibi_claim": "كنت في البيت", "alibi_truth": "كنت في البيت" if i != 2 else "كنت في المطبخ",
            "secret": "سر", "knows": "حاجة"} for i in range(1, 5)]
    types = [("decisive", "s2"), ("decisive", "s2"), ("motive", "s2"),
             ("red_herring", "s1"), ("red_herring", "s3"), ("exonerating", "s4")]
    clues = [{"id": f"c{i+1}", "title": f"دليل {i+1}", "description": "وصف", "type": t,
              "points_to": p, "suspect_reaction": "يتوتر"} for i, (t, p) in enumerate(types)]
    locs = [{"id": f"l{i+1}", "name": f"مكان {i+1}", "description": "وصف",
             "clue_ids": [f"c{i+1}"] if i < 3 else ["c4", "c5", "c6"]} for i in range(4)]
    return {"title": "قضية تجريبية", "intro": "حصلت جريمة", "setting": "فيلا",
            "victim": {"name": "الضحية", "description": "رجل أعمال"}, "time_of_death": "منتصف الليل",
            "truth": "القاتل هو شخص 2", "killer_id": "s2", "suspects": sus, "clues": clues,
            "locations": locs}


def fake_chat(messages, **kw):
    system = messages[0]["content"]
    if "Create an original murder-mystery" in messages[1]["content"]:
        return json.dumps(fake_case(), ensure_ascii=False)
    if "قاضي" in system:
        return json.dumps({"reasoning_score": 22, "verdict": "اتكشف القاتل.", "feedback": "شغل كويس"},
                          ensure_ascii=False)
    return json.dumps({"reply": "أنا معملتش حاجة!", "mood": "nervous"}, ensure_ascii=False)


llm.chat = fake_chat
from backend.main import app  # noqa: E402

c = TestClient(app)
assert c.get("/").status_code == 200
assert c.get("/api/health").json()["ok"]

d = c.post("/api/case/daily").json()
tok, case = d["token"], d["case"]
assert "killer_id" not in case and "truth" not in json.dumps(case, ensure_ascii=False)
assert "alibi_truth" not in json.dumps(case, ensure_ascii=False)
d2 = c.post("/api/case/daily").json()          # نفس قضية اليوم
assert d2["case"]["case_id"] == case["case_id"]

r = c.post("/api/search", json={"token": tok, "location_id": "l1"}).json()
assert r["clues"] and "type" not in r["clues"][0] and "points_to" not in r["clues"][0]

r = c.post("/api/chat", json={"token": tok, "suspect_id": "s2", "message": "كنت فين؟",
                              "history": [{"role": "user", "content": "اهلا"},
                                          {"role": "assistant", "content": "اهلا"}]}).json()
assert r["reply"] and r["mood"] == "nervous"
r = c.post("/api/chat", json={"token": tok, "suspect_id": "s2", "message": "",
                              "evidence_id": "c1"}).json()
assert r["reply"]

assert c.post("/api/chat", json={"token": "garbage", "suspect_id": "s2", "message": "x"}).status_code == 400
assert c.post("/api/chat", json={"token": tok, "suspect_id": "zz", "message": "x"}).status_code == 404

r = c.post("/api/accuse", json={"token": tok, "suspect_id": "s2", "reasoning": "الدليل 1 بيكذّب كلامه",
                                "questions": 5}).json()
assert r["correct"] and r["score"] == 50 + 22 + 20 and r["killer"]["id"] == "s2", r
r = c.post("/api/accuse", json={"token": tok, "suspect_id": "s1", "reasoning": "", "questions": 5}).json()
assert not r["correct"] and r["score"] == 0

n = c.post("/api/case/new").json()
assert n["case"]["case_id"] != case["case_id"]
print("✅ كل الاختبارات نجحت")
