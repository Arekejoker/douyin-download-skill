import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import dy_common as C  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


class ExtractCandidateTest(unittest.TestCase):
    def test_share_text_cases(self):
        with open(os.path.join(FIX, "share_texts.json"), encoding="utf-8") as f:
            cases = json.load(f)
        for c in cases:
            kind, val = C.extract_candidate(c["input"])
            self.assertEqual(kind, c["kind"], c["input"])
            if c.get("url_contains"):
                self.assertIn(c["url_contains"], val)


class ParseAwemeUrlTest(unittest.TestCase):
    def test_variants(self):
        cases = [
            ("https://www.douyin.com/video/7412345678901234567",
             ("7412345678901234567", "video")),
            ("https://www.douyin.com/note/7412345678901234567/",
             ("7412345678901234567", "note")),
            ("https://www.douyin.com/share/video/7412345678901234567/",
             ("7412345678901234567", "video")),
            ("https://www.iesdouyin.com/share/note/7412345678901234567/?region=CN",
             ("7412345678901234567", "note")),
            ("https://www.douyin.com/?modal_id=7412345678901234567",
             ("7412345678901234567", None)),
            ("https://v.douyin.com/iRNBho6/", (None, None)),
        ]
        for url, expected in cases:
            self.assertEqual(C.parse_aweme_url(url), expected, url)


class ResolveOfflineTest(unittest.TestCase):
    def test_pure_id(self):
        res = C.resolve_input("7412345678901234567")
        self.assertTrue(res["ok"])
        self.assertEqual(res["aweme_id"], "7412345678901234567")
        self.assertIsNone(res["type"])

    def test_invalid(self):
        res = C.resolve_input("hello world")
        self.assertFalse(res["ok"])
        self.assertEqual(res["errors"][0]["code"], "invalid_input")

    def test_long_url_no_network(self):
        res = C.resolve_input("https://www.douyin.com/note/7412345678901234567")
        self.assertTrue(res["ok"])
        self.assertEqual(res["type"], "note")

    def test_profile_url(self):
        res = C.resolve_input("https://www.douyin.com/user/MS4wLjABAAAAxyz")
        self.assertFalse(res["ok"])
        self.assertEqual(res["errors"][0]["code"], "unsupported_profile")


class UpgradeImageTest(unittest.TestCase):
    def test_cropcenter_upgraded(self):
        u = ("https://p3.douyinpic.com/x~tplv-dy-cropcenter:300:400:5:5.webp"
             "?x-expires=1&x-signature=A")
        up = C.upgrade_image_url(u)
        self.assertIn("~tplv-dy-aweme-images:q75.webp", up)
        self.assertIn("x-signature=A", up)
        self.assertIn("x-expires=1", up)

    def test_q75_kept(self):
        u = "https://p3.douyinpic.com/x~tplv-dy-aweme-images:q75.webp?a=1"
        self.assertEqual(C.upgrade_image_url(u), u)

    def test_no_template_untouched(self):
        u = "https://p3.douyinpic.com/obj/comment.jpeg?a=1"
        self.assertEqual(C.upgrade_image_url(u), u)


class TopicsTest(unittest.TestCase):
    def test_dedupe_and_separators(self):
        got = C.topics_from("标题 #猫眼睛睁不开", "正文 #猫眼睛睁不开 #日常")
        self.assertEqual(got, ["#猫眼睛睁不开", "#日常"])


class StatusTest(unittest.TestCase):
    def test_success(self):
        self.assertEqual(C.finalize_status([], {"text", "image"},
                                           {"text", "image"}), "success")

    def test_partial(self):
        self.assertEqual(C.finalize_status([], {"text", "image", "video"},
                                           {"text", "image"}), "partial")

    def test_failed_no_artifacts(self):
        self.assertEqual(C.finalize_status([], {"image"}, set()), "failed")

    def test_blocked_is_failed(self):
        e = [C.err("blocked_by_verification", "captcha")]
        self.assertEqual(C.finalize_status(e, {"image"}, set()), "failed")

    def test_warning_not_fatal(self):
        w = [C.warn("ytdlp_failed", "x")]
        self.assertEqual(C.finalize_status(w, {"video"}, {"video"}), "success")


class BlockDetectTest(unittest.TestCase):
    def test_media_present_wins(self):
        data = {"title": "验证", "bodySample": "滑块验证", "imgs": ["u"],
                "video": "", "desc": ""}
        self.assertIsNone(C.detect_block(data))

    def test_captcha(self):
        data = {"title": "", "bodySample": "请完成下列验证后继续", "imgs": [],
                "video": "", "desc": ""}
        self.assertEqual(C.detect_block(data), "blocked_by_verification")

    def test_login_wall(self):
        data = {"title": "", "bodySample": "登录后查看", "imgs": [],
                "video": "", "desc": ""}
        self.assertEqual(C.detect_block(data), "login_required")


class BuildMetaTest(unittest.TestCase):
    def _res(self, t="note"):
        return {"aweme_id": "7412345678901234567", "type": t,
                "input": "x", "source_url": "https://v.douyin.com/x/",
                "resolved_url": None}

    def test_note_fixture(self):
        with open(os.path.join(FIX, "dom_note.json"), encoding="utf-8") as f:
            data = json.load(f)
        meta = C.build_meta(self._res(), data)
        self.assertEqual(meta["title"], "泪水打湿小猫粮，咪我要做小猫王#猫眼睛睁不开")
        self.assertEqual(meta["author"], "小猫日记")
        self.assertEqual(meta["stats"]["like"], "12.3万")
        self.assertEqual(len(meta["urls"]["images"]), 2)
        self.assertIn("#猫眼睛睁不开", meta["topics"])
        self.assertEqual(meta["music"], "小猫圆舞曲 - 猫猫乐队")

    def test_video_fixture(self):
        with open(os.path.join(FIX, "dom_video.json"), encoding="utf-8") as f:
            data = json.load(f)
        meta = C.build_meta(self._res("video"), data)
        self.assertEqual(meta["title"], "海边的日落")
        self.assertIn("video_id=", meta["urls"]["video"])
        self.assertEqual(meta["urls"]["images"], [])

    def test_desc_falls_back_to_title(self):
        data = {"title": "作品标题 #话题 - 抖音", "desc": "", "imgs": []}
        meta = C.build_meta(self._res(), data)
        self.assertEqual(meta["desc"], "作品标题 #话题")


class ApiItemTest(unittest.TestCase):
    def test_shape(self):
        with open(os.path.join(FIX, "api_item.json"), encoding="utf-8") as f:
            item = json.load(f)
        data = C.api_item_to_dom_shape(item, "https://x", "note")
        self.assertEqual(data["imgs"], ["https://img.example/1.jpeg"])
        self.assertNotIn("playwm", data["video"])
        self.assertIn("play/", data["video"])
        self.assertEqual(data["audio"], "https://m.example/bgm.mp3")
        self.assertEqual(data["cover"], "https://img.example/cover.jpeg")
        self.assertEqual(data["stats"]["like"], "100")
        self.assertTrue(data["published"])


if __name__ == "__main__":
    unittest.main()
