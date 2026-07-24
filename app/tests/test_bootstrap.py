"""模型配置契约启动校验测试。"""

from types import SimpleNamespace

from app.utils import bootstrap


def test_runtime_validation_reports_missing_vision_model(monkeypatch, tmp_path):
    """应用启动前必须配置两个视觉模型层级。"""

    settings = SimpleNamespace(dashscope_api_key="test-key")
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)
    main_prompt = tmp_path / "main.txt"
    report_prompt = tmp_path / "report.txt"
    data_path = tmp_path / "data"
    main_prompt.write_text("main", encoding="utf-8")
    report_prompt.write_text("report", encoding="utf-8")
    data_path.mkdir()

    monkeypatch.setattr(
        bootstrap,
        "get_prompts_config",
        lambda: {"main_prompt_path": str(main_prompt), "report_prompt_path": str(report_prompt)},
    )
    monkeypatch.setattr(
        bootstrap,
        "get_vector_store_config",
        lambda: {
            "collection_alias": "rag_active",
            "url": "http://qdrant",
            "grpc_port": 6334,
            "prefer_grpc": True,
            "data_path": str(data_path),
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "get_models_config",
        lambda: {
            "chat_model_name": "chat",
            "embedding_model_name": "embedding",
            "vl_primary_model_name": "qwen-vl-plus",
        },
    )
    monkeypatch.setattr(bootstrap, "get_abs_path", lambda relative_path: relative_path)

    issues = bootstrap.validate_runtime()

    assert "模型配置缺失：vl_fallback_model_name" in issues
