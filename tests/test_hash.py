from app.services.hash import sha256_file


def test_sha256_file(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"document-lab")
    assert sha256_file(path) == "5259cc4c73cb718fb69f4e5becf35f3d63be7513d905e69bab01d64d3bdc5303"
