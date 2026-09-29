"""
tests/test_asset_preloader.py — ui/asset_preloader.py (28/09/2026).
`preload_all()` varre `assets/` inteiro e carrega cada imagem/som pelo
cache compartilhado (ui/asset_cache.py) — testado aqui contra uma
pasta de assets FAKE (não o `assets/` real do projeto, que tem
centenas de arquivos e deixaria o teste lento e frágil a mudanças de
conteúdo)."""
import os, sys
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil
import tempfile
import unittest
import pygame

pygame.init()
pygame.display.set_mode((320, 240))
pygame.mixer.init()

from ui import asset_cache
from ui.asset_preloader import preload_all, iter_asset_files


def _make_fake_png(path: str) -> None:
    surf = pygame.Surface((4, 4), pygame.SRCALPHA)
    surf.fill((10, 20, 30, 255))
    pygame.image.save(surf, path)


class TestPreloadAll(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="rpgpreload_")
        self._orig_resource_path = None
        asset_cache._images.clear()
        asset_cache._sounds.clear()

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _patch_resource_path(self, monkeypatch_target: str) -> None:
        import paths
        self._orig_resource_path = paths.resource_path
        paths.resource_path = lambda rel: self._tmpdir if rel == "assets" else self._orig_resource_path(rel)

    def _restore_resource_path(self) -> None:
        import paths
        if self._orig_resource_path is not None:
            paths.resource_path = self._orig_resource_path

    def test_carrega_todas_as_imagens_de_uma_pasta_fake(self):
        os.makedirs(os.path.join(self._tmpdir, "sub"), exist_ok=True)
        _make_fake_png(os.path.join(self._tmpdir, "a.png"))
        _make_fake_png(os.path.join(self._tmpdir, "sub", "b.png"))
        # arquivo de extensão irrelevante — não deve contar nem tentar carregar
        with open(os.path.join(self._tmpdir, "readme.txt"), "w") as f:
            f.write("nao é asset")

        self._patch_resource_path("assets")
        try:
            result = preload_all()
        finally:
            self._restore_resource_path()

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["images"], 2)
        self.assertEqual(result["failed"], [])
        self.assertEqual(asset_cache.cached_image_count(), 2)

    def test_arquivo_de_imagem_corrompido_entra_em_failed_mas_nao_lanca(self):
        bad_path = os.path.join(self._tmpdir, "corrompido.png")
        with open(bad_path, "wb") as f:
            f.write(b"isto nao e um png de verdade")

        self._patch_resource_path("assets")
        try:
            result = preload_all()
        finally:
            self._restore_resource_path()

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["images"], 0)
        self.assertEqual(result["failed"], [bad_path])

    def test_progress_cb_chamado_com_done_e_total_corretos(self):
        for i in range(20):
            _make_fake_png(os.path.join(self._tmpdir, f"img_{i}.png"))

        calls = []
        self._patch_resource_path("assets")
        try:
            preload_all(progress_cb=lambda done, total: calls.append((done, total)), step=5)
        finally:
            self._restore_resource_path()

        self.assertTrue(calls)
        self.assertEqual(calls[-1], (20, 20))
        for done, total in calls:
            self.assertEqual(total, 20)
            self.assertLessEqual(done, total)

    def test_pasta_assets_ausente_nao_lanca_e_devolve_total_zero(self):
        import paths
        orig = paths.resource_path
        paths.resource_path = lambda rel: "/caminho/que/nao/existe/de/verdade" if rel == "assets" else orig(rel)
        try:
            result = preload_all()
        finally:
            paths.resource_path = orig
        self.assertEqual(result["total"], 0)

    def test_iter_asset_files_ignora_extensoes_nao_reconhecidas(self):
        os.makedirs(self._tmpdir, exist_ok=True)
        _make_fake_png(os.path.join(self._tmpdir, "a.png"))
        with open(os.path.join(self._tmpdir, "b.txt"), "w") as f:
            f.write("x")
        with open(os.path.join(self._tmpdir, "c.ogg"), "wb") as f:
            f.write(b"\x00")
        files = iter_asset_files(self._tmpdir)
        kinds = sorted(k for _p, k in files)
        self.assertEqual(kinds, ["image", "sound"])


if __name__ == "__main__":
    unittest.main()
