def test_package_imports():
    import deployground

    assert isinstance(deployground.__version__, str)
    assert deployground.__version__
