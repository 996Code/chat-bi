"""
T016-preC: LLM client 封装 (AsyncOpenAI) + 中文推断

对标: milvus_client.py / redis_client.py 的单例封装范式。
讯飞 MAAS 用 OpenAI 兼容协议 (base_url + api_key), 直接用 AsyncOpenAI 对接。

关键: infer_column_chinese 失败要降级为空 dict 而非抛异常
(宁缺毋滥: LLM 挂了不能阻塞扫描, 退化用列名即可)。
"""
import pytest


class TestLLMClient:
    """get_llm_client / get_embedding_client 单例 + 配置来自 settings。"""

    def test_llm_client_returns_async_openai(self):
        from app.core.llm_client import get_llm_client
        from openai import AsyncOpenAI

        client = get_llm_client()
        assert isinstance(client, AsyncOpenAI)

    def test_llm_client_is_singleton(self):
        from app.core.llm_client import get_llm_client

        c1 = get_llm_client()
        c2 = get_llm_client()
        assert c1 is c2

    def test_embedding_client_returns_async_openai(self):
        from app.core.llm_client import get_embedding_client
        from openai import AsyncOpenAI

        client = get_embedding_client()
        assert isinstance(client, AsyncOpenAI)

    def test_reset_clients_for_testing(self):
        """测试可重置单例（避免测试间状态泄漏）。"""
        from app.core.llm_client import get_llm_client, reset_clients

        c1 = get_llm_client()
        reset_clients()
        c2 = get_llm_client()
        assert c1 is not c2


class TestInferColumnChinese:
    """infer_column_chinese: 调 LLM 补中文 display_name。

    不真实调用 LLM（测试用 monkeypatch mock client）。
    关键: 失败降级为空 dict, 不抛异常。
    """

    @pytest.mark.asyncio
    async def test_infer_returns_mapping(self, monkeypatch):
        """正常调用 → 返回 {列名: 中文名} 映射。"""
        from app.core import llm_client

        class FakeResp:
            class choices:
                class message:
                    content = '{"id": "编号", "username": "用户名", "city": "城市"}'
            choices = [type("C", (), {"message": type("M", (), {"content": '{"id": "编号", "username": "用户名", "city": "城市"}'})()})()]

        async def fake_create(*args, **kwargs):
            return FakeResp()

        mock_client = type("MC", (), {"chat": type("CH", (), {"completions": type("CO", (), {"create": fake_create})()})()})()
        monkeypatch.setattr(llm_client, "get_llm_client", lambda: mock_client)

        result = await llm_client.infer_column_chinese(
            "users",
            [{"name": "id"}, {"name": "username"}, {"name": "city"}],
        )
        assert result["username"] == "用户名"
        assert result["city"] == "城市"

    @pytest.mark.asyncio
    async def test_infer_failure_returns_empty_dict(self, monkeypatch):
        """LLM 调用失败 → 返回空 dict，不抛异常（宁缺毋滥降级）。"""
        from app.core import llm_client

        async def fake_create(*args, **kwargs):
            raise Exception("LLM service unavailable")

        mock_client = type("MC", (), {"chat": type("CH", (), {"completions": type("CO", (), {"create": fake_create})()})()})()
        monkeypatch.setattr(llm_client, "get_llm_client", lambda: mock_client)

        result = await llm_client.infer_column_chinese("t", [{"name": "x"}])
        assert result == {}  # 降级: 空, 不抛

    @pytest.mark.asyncio
    async def test_infer_malformed_json_returns_empty_dict(self, monkeypatch):
        """LLM 返回非法 JSON → 空 dict（降级）。"""
        from app.core import llm_client

        async def fake_create(*args, **kwargs):
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "not a json"})()})()]})()

        mock_client = type("MC", (), {"chat": type("CH", (), {"completions": type("CO", (), {"create": fake_create})()})()})()
        monkeypatch.setattr(llm_client, "get_llm_client", lambda: mock_client)

        result = await llm_client.infer_column_chinese("t", [{"name": "x"}])
        assert result == {}
