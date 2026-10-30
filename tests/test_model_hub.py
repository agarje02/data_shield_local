import pytest

from datashield_local.engine.model_hub import verify, write_sums


def test_checksums_catch_tampering_and_missing_files(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"weights")
    (tmp_path / "labels.yaml").write_text("labels: [PERSON]")
    assert write_sums(tmp_path) == 2
    verify(tmp_path)                                   # untouched: passes
    (tmp_path / "model.onnx").write_bytes(b"weightz")
    with pytest.raises(ValueError, match="model.onnx"):
        verify(tmp_path)
    (tmp_path / "model.onnx").unlink()
    with pytest.raises(ValueError):
        verify(tmp_path)


def test_unverified_model_refused(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"x")
    with pytest.raises(ValueError, match="SHA256SUMS missing"):
        verify(tmp_path)
