"""
T014-preB: 动态业务库引擎 — DataSource → engine → inspector

对标 v1 教训 #42: 连接池 close_all 只删字典不 dispose → 连接泄漏。
本项目需根据 DataSource 配置动态连接用户的业务库（扫描/查询都要用）。

注意: SQLAlchemy inspect() 是同步 API, 这里用同步 create_engine (psycopg2/mysqlconnector)，
而非 session.py 的 async create_async_engine。T013 端到端验证已用此模式。

测试不连真库: build_engine_url 用断言验证拼接；引擎池用 mock。
"""
import pytest


class TestBuildEngineUrl:
    """按方言拼正确的同步连接串。"""

    def test_postgresql_url(self):
        from app.services.datasource_engine import build_engine_url

        url = build_engine_url(
            db_type="postgresql", host="10.0.0.1", port=5432,
            database="mydb", username="user1", password="pass1",
        )
        assert url == "postgresql+psycopg2://user1:pass1@10.0.0.1:5432/mydb"

    def test_mysql_url(self):
        from app.services.datasource_engine import build_engine_url

        url = build_engine_url(
            db_type="mysql", host="10.0.0.2", port=3306,
            database="mydb", username="u", password="p@ss",
        )
        assert url == "mysql+pymysql://u:p%40ss@10.0.0.2:3306/mydb"

    def test_password_url_encoded(self):
        """密码含特殊字符（@/#/:）需 URL 编码，否则破坏连接串。"""
        from app.services.datasource_engine import build_engine_url

        url = build_engine_url(
            db_type="postgresql", host="h", port=5432,
            database="d", username="u", password="p@ss:w/ord#",
        )
        # 特殊字符被编码, 不破坏 URL 结构
        assert "p%40ss" in url  # @ -> %40
        assert ":w" not in url.split("@")[0] or "%2F" in url  # / 被编码


class TestDataSourceToUrl:
    """DataSource ORM 行 → 解密密码 → 拼 URL。"""

    def test_datasource_row_to_url(self):
        """给一个 DataSource 对象（含加密密码），解密后拼出正确 URL。"""
        from app.services.datasource_engine import datasource_to_url
        from app.core.security import encrypt_password

        # 构造假 DataSource（duck typing, 不需要真 ORM 实例）
        class FakeDS:
            db_type = "postgresql"
            host = "192.168.99.22"
            port = 5432
            database = "njmind"
            username = "njmind"
            encrypted_password = encrypt_password("secret123")

        url = datasource_to_url(FakeDS())
        assert "postgresql+psycopg2://njmind:secret123@192.168.99.22:5432/njmind" == url


class TestEnginePool:
    """引擎池: 按 datasource_id 缓存, 可释放 (对标 #42 连接泄漏)。"""

    def test_pool_caches_by_datasource_id(self):
        """同一 datasource_id 第二次获取应命中缓存（同一个 engine 对象）。"""
        from app.services.datasource_engine import DataSourceEnginePool

        pool = DataSourceEnginePool()
        # 用 mock build_engine_url 避免真连库
        id1 = "ds_001"
        e1 = pool.get_or_create(id1, "sqlite://", dialect_override="sqlite")
        e2 = pool.get_or_create(id1, "sqlite://", dialect_override="sqlite")
        assert e1 is e2  # 同一对象 = 缓存命中

    def test_different_ids_different_engines(self):
        from app.services.datasource_engine import DataSourceEnginePool

        pool = DataSourceEnginePool()
        e1 = pool.get_or_create("ds_a", "sqlite://", dialect_override="sqlite")
        e2 = pool.get_or_create("ds_b", "sqlite://", dialect_override="sqlite")
        assert e1 is not e2

    def test_dispose_releases_engine(self):
        """dispose 后再获取应创建新 engine（旧的被释放）。"""
        from app.services.datasource_engine import DataSourceEnginePool

        pool = DataSourceEnginePool()
        e1 = pool.get_or_create("ds_x", "sqlite://", dialect_override="sqlite")
        pool.dispose("ds_x")
        e2 = pool.get_or_create("ds_x", "sqlite://", dialect_override="sqlite")
        assert e1 is not e2  # 新对象 = 旧的已释放

    def test_dispose_all(self):
        from app.services.datasource_engine import DataSourceEnginePool

        pool = DataSourceEnginePool()
        pool.get_or_create("a", "sqlite://", dialect_override="sqlite")
        pool.get_or_create("b", "sqlite://", dialect_override="sqlite")
        assert len(pool._engines) == 2
        pool.dispose_all()
        assert len(pool._engines) == 0
