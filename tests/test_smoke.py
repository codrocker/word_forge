"""Import-smoke: verify the package loads cleanly under uv-built env."""


def test_import_wordforge():
    import wordforge  # noqa: F401
