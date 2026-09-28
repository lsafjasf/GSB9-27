"""回归测试：失败/跳过路径的清理断言 + 并发隔离断言。"""
import os
import threading
import unittest

from fixture_fixed import IsolatedFixture

KEY = "GSB_FIXTURE_PROFILE"


class CleanupOnFailureTest(unittest.TestCase):
    def test_cleanup_on_exception(self):
        fx = IsolatedFixture("boom", uses_env=True)
        with self.assertRaises(RuntimeError):
            with fx:
                fx.set_env(KEY, "testing")
                fx.registry["k"] = 1
                open(os.path.join(fx.tmpdir, "f.tmp"), "w").close()
                tmpdir = fx.tmpdir
                raise RuntimeError("boom")
        self.assertFalse(os.path.exists(tmpdir), "异常后临时目录必须被删除")
        self.assertNotIn(KEY, os.environ, "异常后环境变量必须被还原")

    def test_cleanup_on_skip(self):
        fx = IsolatedFixture("skipping", uses_env=True)
        with self.assertRaises(unittest.SkipTest):
            with fx:
                fx.set_env(KEY, "testing")
                open(os.path.join(fx.tmpdir, "f.tmp"), "w").close()
                tmpdir = fx.tmpdir
                raise unittest.SkipTest("被跳过")
        self.assertFalse(os.path.exists(tmpdir), "跳过后临时目录必须被删除")
        self.assertNotIn(KEY, os.environ, "跳过后环境变量必须被还原")

    def test_env_preexisting_value_restored(self):
        os.environ[KEY] = "original"
        self.addCleanup(os.environ.pop, KEY, None)
        with IsolatedFixture("restore", uses_env=True) as fx:
            fx.set_env(KEY, "changed")
            self.assertEqual(os.environ[KEY], "changed")
        self.assertEqual(os.environ[KEY], "original", "预先存在的值必须被还原")

    def test_close_is_idempotent(self):
        fx = IsolatedFixture("idem")
        with fx:
            tmpdir = fx.tmpdir
        fx.close()
        fx.close()
        self.assertFalse(os.path.exists(tmpdir))

    def test_cache_is_per_fixture(self):
        with IsolatedFixture("c1") as fx1:
            self.assertEqual(fx1.cached_lookup("k", "v1"), "v1")
            self.assertEqual(fx1.lookup_cache, {"k": "v1"})
        with IsolatedFixture("c2") as fx2:
            self.assertEqual(fx2.lookup_cache, {}, "新夹具的缓存必须是冷的")
            self.assertEqual(fx2.cached_lookup("k", "v2"), "v2")


class ConcurrencyIsolationTest(unittest.TestCase):
    def test_concurrent_fixtures_do_not_interfere(self):
        n_threads = 16
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(i):
            key = "GSB_WORKER_%d" % i
            try:
                with IsolatedFixture("w%d" % i) as fx:
                    fx.set_env(key, "v%d" % i)
                    fx.registry["id"] = i
                    fx.counter += 1
                    path = os.path.join(fx.tmpdir, "data.txt")
                    with open(path, "w") as f:
                        f.write(str(i))
                    barrier.wait(timeout=10)  # 让所有线程的污染同时存在
                    assert os.environ[key] == "v%d" % i
                    assert fx.registry == {"id": i}
                    assert fx.counter == 1
                    with open(path) as f:
                        assert f.read() == str(i)
                    assert len(os.listdir(fx.tmpdir)) == 1
                assert not os.path.exists(fx.tmpdir)
                assert key not in os.environ
            except Exception as e:  # noqa: BLE001
                errors.append("worker %d: %r" % (i, e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
