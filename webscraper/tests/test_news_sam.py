"""Offline tests for news-image SAM path (no network, no keys)."""
import base64, io, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "traffic-cameras"))

from main_country import parse_article_images, road_near_image, download_image

HTML = """
<html><head><meta property="og:image" content="https://ex.com/lead.jpg"></head>
<body><p>Flooding on the A1 near Durham.</p>
<figure><img src="https://ex.com/a1.jpg" alt="A1 flood water">
<figcaption>Water covers the A1 carriageway</figcaption></figure>
<img src="https://ex.com/other.jpg" alt="unrelated photo">
</body></html>"""


def _png(white_frac: float) -> bytes:
    from PIL import Image
    im = Image.new("L", (10, 10), 0)
    px = im.load()
    n = int(100 * white_frac)
    for i in range(n):
        px[i % 10, i // 10] = 255
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


class TestNewsImages(unittest.TestCase):
    def test_parse_article_images(self):
        imgs = parse_article_images(HTML)
        urls = [i["url"] for i in imgs]
        self.assertIn("https://ex.com/lead.jpg", urls)
        self.assertIn("https://ex.com/a1.jpg", urls)
        a1 = next(i for i in imgs if "a1.jpg" in i["url"])
        self.assertIn("A1", a1["context"])

    def test_road_near_image(self):
        imgs = parse_article_images(HTML)
        hit = road_near_image("A1, Durham", imgs)
        self.assertIsNotNone(hit)
        self.assertIn("a1.jpg", hit["url"])
        self.assertIsNone(road_near_image("M6", imgs))
        self.assertIsNone(road_near_image("", imgs))
        self.assertIsNone(road_near_image("A1", ["https://ex.com/x.jpg"]))

    def test_download_rejects(self):
        self.assertIsNone(download_image("not-a-url"))
        self.assertIsNone(download_image("http://127.0.0.1:9/nope.jpg", timeout=3))

    def test_sam_water_fraction(self):
        from vision import sam_water_fraction, sam_flood_check
        self.assertEqual(sam_water_fraction(_png(1.0)), 1.0)
        self.assertEqual(sam_water_fraction(_png(0.0)), 0.0)
        self.assertAlmostEqual(sam_water_fraction(_png(0.5)), 0.5)
        self.assertIsNone(sam_water_fraction(b"not-png"))

    def test_sam_flood_check_mocked(self):
        import vision
        real = vision._sam_mask_bytes
        vision._sam_mask_bytes = lambda b: (_png(0.5), {})
        try:
            out = vision.sam_flood_check(b"x", None)
        finally:
            vision._sam_mask_bytes = real
        self.assertTrue(out["flooded"])
        self.assertEqual(out["water_fraction"], 0.5)
        self.assertIsNone(out["mask_path"])
        self.assertTrue(out["sam_ran"])

    def test_sam_flood_check_no_key(self):
        import vision
        real = vision._sam_mask_bytes
        vision._sam_mask_bytes = lambda b: (None, {})
        try:
            out = vision.sam_flood_check(b"x", None)
        finally:
            vision._sam_mask_bytes = real
        self.assertFalse(out["flooded"])
        self.assertEqual(out["confidence"], 0.0)
        self.assertFalse(out["sam_ran"])

    def test_check_news_image_prefers_sam(self):
        import vision
        real_mask, real_cls = vision._sam_mask_bytes, vision.classify_flood
        vision._sam_mask_bytes = lambda b: (self._png_bytes(0.5), {})
        try:
            out = vision.check_news_image(b"x", None)
        finally:
            vision._sam_mask_bytes, vision.classify_flood = real_mask, real_cls
        self.assertEqual(out["backend"], "sam-3.1")
        self.assertTrue(out["flooded"])

    def test_check_news_image_falls_back_to_baseten_vision(self):
        import vision
        real_mask, real_cls = vision._sam_mask_bytes, vision.classify_flood
        vision._sam_mask_bytes = lambda b: (None, {})
        vision.classify_flood = lambda b, s=None: {"flooded": True, "confidence": 0.8,
                                                   "evidence": "v", "mask_path": None}
        os.environ["USE_META_VISION"] = "1"
        os.environ["BASETEN_API_KEY"] = "k"
        try:
            out = vision.check_news_image(b"x", None)
        finally:
            vision._sam_mask_bytes, vision.classify_flood = real_mask, real_cls
            os.environ.pop("USE_META_VISION", None)
            os.environ.pop("BASETEN_API_KEY", None)
        self.assertTrue(out["backend"].startswith("baseten:"))
        self.assertTrue(out["flooded"])
        self.assertIsNone(out["water_fraction"])

    def test_check_news_image_no_backend(self):
        import vision
        real_mask, real_en = vision._sam_mask_bytes, vision.enabled
        vision._sam_mask_bytes = lambda b: (None, {})
        vision.enabled = lambda: False
        try:
            out = vision.check_news_image(b"x", None)
        finally:
            vision._sam_mask_bytes, vision.enabled = real_mask, real_en
        self.assertEqual(out["backend"], "none")
        self.assertFalse(out["flooded"])

    def _png_bytes(self, frac):
        import io
        from PIL import Image
        im = Image.new("L", (10, 10), 0)
        px = im.load()
        n = int(100 * frac)
        for i in range(n):
            px[i % 10, i // 10] = 255
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


if __name__ == "__main__":
    unittest.main()
